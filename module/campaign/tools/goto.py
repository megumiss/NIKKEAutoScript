"""Closed-loop walk to a visible normal enemy, skipping EX stages.

Localization: template-match the current expanded minimap against the cached chapter map.
If that match fails, use visible enemy/self markers in the same minimap frame.
Click mapping: A_INV @ world_delta in the calibrated reference plane.
Battle popup: match the enter-battle button and exit without further clicks.
"""

import json
from pathlib import Path

import argparse

from module.campaign.navigation.controls import (
    terrain as terrain,
    map_open as map_open,
    map_close as map_close,
    capture_client as capture_client,
    battle_popup_score as battle_popup_score,
    normal_enemy_markers as normal_enemy_markers,
    movement_click as movement_click,
    ASSETS as ASSETS,
    MAP_MINIMIZE as MAP_MINIMIZE,
    A_INV as A_INV,
    ROI_TO_PLANE as ROI_TO_PLANE,
    VIEWPORT_PIXEL as VIEWPORT_PIXEL,
    GAZE as GAZE,
    ARROW_GROUND_OFFSET as ARROW_GROUND_OFFSET,
    EXPANDED_CENTER as EXPANDED_CENTER,
    SQUAD_REACH as SQUAD_REACH,
    SQUAD_REACH_WIDE as SQUAD_REACH_WIDE,
    ENTER_BATTLE_TPL as ENTER_BATTLE_TPL,
    EX_STAGE_TPL as EX_STAGE_TPL,
)
from module.campaign.tools import settings, runtime

import cv2
import numpy as np

from module.campaign.vision import perception

ARGS = settings.driver_args()
def check_battle_popup(win, log, iteration, phase, image=None):
    """普通准备弹窗保留供上层处理；EX 弹窗只关闭一次并确认结果。

    使用已采集的 image（缺省时重新截图）确认准备按钮，区分普通和 EX 并保存截图与日志。
    无弹窗返回 None，普通弹窗保留；EX 仅关闭一次并复查，关闭失败抛错。
    """
    if image is None:
        image = capture_client(win)
    score = battle_popup_score(image)
    if score <= 0.8:
        return None
    ex_result = cv2.matchTemplate(image, EX_STAGE_TPL, cv2.TM_CCOEFF_NORMED)
    ex_score = float(cv2.minMaxLoc(ex_result)[1])
    status = 'ex_stage_skipped' if ex_score > 0.85 else 'battle_popup'
    screenshot = settings.output / f'battle_popup_{iteration}_{phase}.png'
    runtime.write_image(str(screenshot), image)
    entry = {'status': status, 'iteration': iteration, 'phase': phase,
             'popup_score': round(score, 4), 'ex_score': round(ex_score, 4), 'screenshot': str(screenshot)}
    log.append(entry)
    print(json.dumps(entry), flush=True)
    if status == 'ex_stage_skipped':
        x, y = win.gui.ClientToScreen(win.hwnd, (0, 0))
        win.handler.mouse_click(x + 1155, y + 521)
        runtime.pause(1.2)
        if battle_popup_score(capture_client(win)) > 0.8:
            raise RuntimeError('EX stage popup did not close.')
    return status






def find_squad(win, reference=None):
    """用短时动画差分估计中央角色位置，供动画诊断使用。

    比较相隔 0.45 秒的客户区图，筛选靠近中心的动画连通块，reference 可约束候选距离。
    返回最靠下的候选中心或 None；该点只用于动画诊断，不提供箭头周期的地面标定语义。
    """
    first = capture_client(win)
    runtime.pause(0.45)
    second = capture_client(win)
    diff = cv2.absdiff(first, second).max(axis=2)
    mask = (diff > 25).astype(np.uint8) * 255
    mask[:150] = 0
    mask[-90:] = 0
    mask[:, :210] = 0
    mask[:, -260:] = 0
    count, _, stats, cents = cv2.connectedComponentsWithStats(mask)
    # Camera follow keeps the squad near the center; remote animations must not drag the anchor away.
    center = np.array([888.0, 499.0])
    blobs = [cents[i] for i in range(1, count)
             if stats[i, cv2.CC_STAT_AREA] >= 40 and np.linalg.norm(cents[i] - center) < 180]
    if not blobs:
        return None
    if reference is not None:
        blobs = [b for b in blobs if np.linalg.norm(b - reference) < 150]
        if not blobs:
            return None
    return np.array(max(blobs, key=lambda b: b[1]))


class Localizer:
    """基于旧 41 章回放缓存的定位器，只用于复现早期实验。

    数据来自 legacy_cache 而不是正式地图包，没有 MapPackage 的哈希绑定与覆盖率校验，
    结果用 coordinate_source='cache' 标记来源，不要用它的定位结论去验收新地图。
    """

    def __init__(self):
        """读取旧 41 章回放缓存并核对截图几何；该缓存没有新地图包的质量保证。

        读取 legacy_cache 的道路概率与投影，要求客户区和 ROI 与运行设置一致。
        仅供 41 章缓存回放，coordinate_source 标记后续结果来源；该缓存不具备 MapPackage 的完整质量校验。
        """
        data = np.load(settings.legacy_cache / 'map_data.npz')
        metadata = json.loads((settings.legacy_cache / 'scan.json').read_text(encoding='utf-8'))
        if metadata['client'] != ARGS.client or metadata['roi'] != ARGS.roi:
            raise ValueError('Cached minimap geometry does not match the current capture settings.')
        # Reuse the cache axes; recalibration can reverse an axis on a different view.
        self.matrix = data['projection']
        self.size = tuple(metadata['warp_size'])
        self.terrain = (data['terrain_probability'] >= 0.5).astype(np.float32)
        self.enemies = []
        self.coordinate_source = 'cache'

    def locate(self, win):
        # Pulsing markers can be clipped or disappear for a few frames.
        """对脉动小队标记进行有限重试，持续失败保存当前帧供离线定位分析。

        最多取得八帧，对脉动圆环造成的短暂漏检间隔 0.3 秒重试。
        成功返回分数、小队坐标和标记数量；耗尽后保存 localization_failed.png，保留 None 位置给上层处理。
        """
        for attempt in range(8):
            image = win.capture()
            score, position, players = self.locate_frame(image)
            if position is not None:
                return score, position, players
            if attempt < 7:
                runtime.pause(0.3)
        runtime.write_image(str(settings.output / 'localization_failed.png'), image)
        return score, position, players

    def locate_frame(self, image):
        """优先匹配旧全局缓存，失败时仅保留同帧敌我相对坐标并明确标注坐标来源。

        优先把同帧普通敌人和小队标记平移到缓存坐标；低分时只保留同帧相对坐标。
        返回匹配分数、可选小队位置和圆环数量，并更新 enemies 与 coordinate_source，调用者必须区分坐标来源。
        """
        mask = cv2.warpPerspective(terrain(image), self.matrix, self.size, flags=cv2.INTER_NEAREST)
        mask = (mask > 0).astype(np.float32)
        result = cv2.matchTemplate(self.terrain, mask, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        players, _ = perception.detect_markers(image, self.matrix, mode='expanded')
        enemies = normal_enemy_markers(image, self.matrix, mode='expanded')
        self_world = None
        self.coordinate_source = 'unlocalized'
        # A tight peak cluster is a confident match; glow/edge effects cap the score near 0.45.
        if score >= 0.4 and len(players) == 1 and enemies:
            self_world = np.array(loc, float) + np.array(players[0])
            # The old enemy table mixes EX and normal stages; select classified visible markers only.
            self.enemies = [np.array(loc, float) + np.array(e) for e in enemies]
            self.coordinate_source = 'cache'
        elif len(players) == 1 and enemies:
            self_world = np.array(players[0], float)
            self.enemies = [np.array(e) for e in enemies]
            self.coordinate_source = 'visible_minimap'
        return score, self_world, len(players)


@runtime.command
def main():
    """只回放旧寻敌定位与普通标记筛选，避免未绑定章节的旧缓存控制游戏。

    使用显式离线输入回放普通敌人检测与缓存定位。
    拒绝将缺少章节绑定的缓存用于现场控制，输出证据用于核对可见标记与坐标来源。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    settings.arguments(parser)
    args = parser.parse_args()
    settings.configure(args)
    image = cv2.imread(str(args.image))
    if image is None:
        raise ValueError('Unreadable minimap replay')
    loc = Localizer()
    score, position, players = loc.locate_frame(image)
    result = {'score': score, 'position': None if position is None else position.tolist(),
              'players': players, 'enemies': [p.tolist() for p in loc.enemies],
              'coordinate_source': loc.coordinate_source, 'status': 'replay_only'}
    print(json.dumps(result), flush=True)
    if position is None:
        raise RuntimeError('Replay localization failed')


if __name__ == '__main__':
    main()
