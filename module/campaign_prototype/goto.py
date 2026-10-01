"""Closed-loop walk to a visible normal enemy, skipping EX stages.

Localization: template-match the current expanded minimap against the cached chapter map.
If that match fails, use visible enemy/self markers in the same minimap frame.
Click mapping: A_INV @ world_delta in the calibrated reference plane.
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
# 客户区位移 = A_INV @ 标定参考平面位移；使用前须核对地图包中的配套矩阵。
A_INV = np.array([[-1.9955, 1.2530], [1.6580, 0.8463]])
ENTER_BATTLE_TPL = cv2.imread(str(ASSETS / 'enter_battle_tpl.png'))
if ENTER_BATTLE_TPL is None:
    raise FileNotFoundError(ASSETS / 'enter_battle_tpl.png')
EX_STAGE_TPL = cv2.imread(str(ASSETS / 'ex_stage_tpl.png'))
if EX_STAGE_TPL is None:
    raise FileNotFoundError(ASSETS / 'ex_stage_tpl.png')


def map_open(win, reset=False):
    """默认保留镜头视野；定位小队时 reset=True 先最小化再打开，使小地图回正。

    通过窗口状态机展开小地图，reset=False 保留镜头寻位后的视野。
    reset=True 用于真实小队观测，先收起再展开以回正地图；切换失败由窗口实现向上传播。
    """
    win.reset_minimap(expanded=True, reset=reset)


def map_close(win):
    """将展开面板收起为紧凑地图，保留地图观测能力。

    请求紧凑地图状态，保留小地图可见以便观察道路变化。
    不切换到完全隐藏状态，也不复位镜头；控件识别与有限重试由 reset_minimap 负责。
    """
    win.reset_minimap(expanded=False, reset=False)


def capture_client(win):
    """检查前台与窗口几何后截取原始客户区，输出 BGR 图供模板和落点分析。

    在确认前台和窗口几何后截取 1776×999 客户区，返回 BGR 数组。
    截图范围使用客户区屏幕原点并允许多显示器；失焦直接失败，不能返回其他窗口画面。
    """
    from PIL import ImageGrab

    win.check()
    if win.gui.GetForegroundWindow() != win.hwnd:
        raise RuntimeError('Game lost focus; client capture stopped.')
    x, y = win.gui.ClientToScreen(win.hwnd, (0, 0))
    image = ImageGrab.grab(bbox=(x, y, x + 1776, y + 999), all_screens=True)
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def battle_popup_score(image):
    """在原始客户区寻找进入战斗按钮，仅返回模板相关分数，不点击按钮。

    image 为 BGR 客户区截图，以进入战斗按钮模板计算归一化相关峰值。
    只返回浮点分数，阈值与后续动作由调用者决定；此函数不发送输入。
    """
    result = cv2.matchTemplate(image, ENTER_BATTLE_TPL, cv2.TM_CCOEFF_NORMED)
    return float(cv2.minMaxLoc(result)[1])


def check_battle_popup(win, log, iteration, phase):
    """普通准备弹窗保留供上层处理；EX 弹窗只关闭一次并确认结果。

    在客户区确认准备按钮后区分普通和 EX 弹窗，保存带 iteration/phase 的截图与日志。
    无弹窗返回 None，普通弹窗保留；EX 仅关闭一次并复查，关闭失败抛错。
    """
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
    """用红色实心轮廓识别普通敌人，排除空心 EX、裁切边缘和计数器后投影中心。

    image 为展开 ROI，matrix 指定 ROI→目标坐标系的透视变换。
    按红色连通轮廓的尺寸、实心程度和边界位置筛选普通敌人，返回投影后中心列表，无候选返回空列表。
    """
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
    """把旧标定平面的位移换成场景点击，统一缩短两轴以保持方向并限制安全范围。

    anchor 为客户区地面点，offset 属于配套场景标定的参考平面位移。
    两轴按同一比例缩放至 240px 及安全边界内，返回浮点落点；锚点本身越界则抛错。
    """
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
