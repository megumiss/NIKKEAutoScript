"""Closed-loop walk to a visible normal enemy, skipping EX stages.

Localization: template-match the current expanded minimap against the cached chapter map.
If that match fails, use visible enemy/self markers in the same minimap frame.
Click mapping: A_inv @ world_delta, measured earlier (see tmp/minimap_move_probe3.log).
Battle popup: match the enter-battle button and exit without further clicks.
"""

import json
from pathlib import Path

import argparse

from . import settings, runtime

import cv2
import numpy as np
from dev_tools import minimap_reconstruct as mr

ARGS = settings.driver_args()
ASSETS = Path(__file__).parent / 'assets'
MAP_MINIMIZE = (644 + 14, 280 - 11)
# screen_offset = A_INV @ world_delta (measured 2026-09-27, tmp/minimap_move_probe3.log)
A_INV = np.array([[-1.9955, 1.2530], [1.6580, 0.8463]])
ENTER_BATTLE_TPL = cv2.imread(str(ASSETS / 'enter_battle_tpl.png'))
if ENTER_BATTLE_TPL is None:
    raise FileNotFoundError(ASSETS / 'enter_battle_tpl.png')
EX_STAGE_TPL = cv2.imread(str(ASSETS / 'ex_stage_tpl.png'))
if EX_STAGE_TPL is None:
    raise FileNotFoundError(ASSETS / 'ex_stage_tpl.png')


def map_open(win):
    """复用采集器状态机进入展开态，已展开时不重置视野。"""
    win.reset_minimap(expanded=True, reset=False)


def map_close(win):
    """沿用兼容名称，但只最小化到紧凑态并确认地图仍可见。"""
    win.reset_minimap(expanded=False, reset=False)


def capture_client(win):
    """检查前台与窗口几何后截取原始客户区，输出 BGR 图供模板和落点分析。"""
    from PIL import ImageGrab

    win.check()
    if win.gui.GetForegroundWindow() != win.hwnd:
        raise RuntimeError('Game lost focus; client capture stopped.')
    x, y = win.gui.ClientToScreen(win.hwnd, (0, 0))
    image = ImageGrab.grab(bbox=(x, y, x + 1776, y + 999), all_screens=True)
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def battle_popup_score(image):
    """在原始客户区寻找进入战斗按钮，仅返回模板相关分数，不点击按钮。"""
    result = cv2.matchTemplate(image, ENTER_BATTLE_TPL, cv2.TM_CCOEFF_NORMED)
    return float(cv2.minMaxLoc(result)[1])


def check_battle_popup(win, log, iteration, phase):
    """普通准备弹窗保留供上层处理；EX 弹窗只关闭一次并确认结果。"""
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


def normal_enemy_markers(image, matrix):
    """用红色实心轮廓识别普通敌人，排除空心 EX、裁切边缘和计数器后投影中心。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, (155, 60, 140), (179, 255, 255)) | cv2.inRange(hsv, (0, 60, 140), (8, 255, 255))
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = red.shape
    markers = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        # Perspective enlarges nearby normal markers; EX outlines have sparse interiors.
        area = cv2.contourArea(contour)
        if not (10 <= w <= 44 and 10 <= h <= 44 and 45 <= area <= 1000):
            continue
        hull_area = cv2.contourArea(cv2.convexHull(contour))
        if area / max(hull_area, 1) < 0.7:
            continue
        filled = np.zeros((h, w), np.uint8)
        cv2.drawContours(filled, [contour - [x, y]], -1, 255, cv2.FILLED)
        if np.count_nonzero(red[y:y + h, x:x + w] & filled) < np.count_nonzero(filled) * 0.65:
            continue
        if x <= 3 or y <= 3 or x + w >= width - 3 or y + h >= height - 3:
            continue
        if x + w >= width - 75 and y + h >= height - 35:
            continue
        moments = cv2.moments(contour)
        markers.append([moments['m10'] / moments['m00'], moments['m01'] / moments['m00']])
    if not markers:
        return []
    return cv2.perspectiveTransform(np.array([markers], np.float32), matrix)[0].round(1).tolist()


def movement_click(anchor, offset):
    """把旧标定平面的位移换成场景点击，统一缩短两轴以保持方向并限制安全范围。"""
    screen_offset = A_INV @ offset
    length = float(np.linalg.norm(screen_offset))
    scale = min(1.0, 240 / max(length, 1))
    # Scale both axes together so safe bounds do not change the walking direction.
    for axis, (lower, upper) in enumerate(((250, 1500), (180, 880))):
        if not lower <= anchor[axis] <= upper:
            raise ValueError('Squad anchor is outside the safe field area.')
        delta = screen_offset[axis]
        if delta > 0:
            scale = min(scale, (upper - anchor[axis]) / delta)
        elif delta < 0:
            scale = min(scale, (lower - anchor[axis]) / delta)
    return anchor + screen_offset * scale


def find_squad(win, reference=None):
    """用短时动画差分估计中央小队位置，仅作为旧原型回放辅助，不能替代脚下地面圆环。"""
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
    def __init__(self):
        """读取旧 41 章回放缓存并核对截图几何；该缓存没有新地图包的质量保证。"""
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
        """对脉动小队标记进行有限重试，持续失败保存当前帧供离线定位分析。"""
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
        """优先匹配旧全局缓存，失败时仅保留同帧敌我相对坐标并明确标注坐标来源。"""
        mask = cv2.warpPerspective(mr.terrain(image), self.matrix, self.size, flags=cv2.INTER_NEAREST)
        mask = (mask > 0).astype(np.float32)
        result = cv2.matchTemplate(self.terrain, mask, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        players, _ = mr.detect_markers(image, self.matrix)
        enemies = normal_enemy_markers(image, self.matrix)
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
    """只回放旧寻敌定位与普通标记筛选，避免未绑定章节的旧缓存控制游戏。"""
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
