"""主线蓝色菱形的模板匹配与场内／离屏区分；不使用 YOLO 识别标记。"""

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

# 模板来自 1776×999 现场截图；颜色只提候选，最终必须通过白底黑感叹号模板。
MATCH_THRESHOLD = .7
CLIENT_SIZE = (1776, 999)
HUD_RECTS = ((0, 0, 1776, 70), (0, 70, 245, 310), (1490, 70, 1776, 205), (0, 900, 1776, 999))


@lru_cache(maxsize=1)
def marker_template():
    """读取新采样的主线模板及有效掩码，忽略菱形外背景和下方会变化的关卡数字。"""
    path = Path(__file__).parent / 'assets/story_marker_tpl.png'
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(path)
    template = cv2.resize(template, (64, 64))
    yy, xx = np.indices(template.shape)
    mask = ((np.abs(xx - 31.5) + np.abs(yy - 31.5) < 29) & (yy < 43)).astype(np.uint8) * 255
    return template, mask


def outward_arrow(image, box):
    """检查菱形旁是否存在指向画外的三角箭头；没有明确的外向三角时返回 False。

    只检查边缘标记附近，不把菱形顶角当箭头。三角必须在图标框外、接近图标、指向邻近画边；
    蓝色或白色三角均可，但 HUD 被排除的区域不会用于点击。
    """
    x1, y1, x2, y2 = box
    center = np.array([(x1 + x2) / 2, (y1 + y2) / 2])
    side = max(x2 - x1, y2 - y1)
    edges = (center[0], center[1], CLIENT_SIZE[0] - center[0], CLIENT_SIZE[1] - center[1])
    if min(edges) > 240:
        return False
    outward = [np.asarray(direction) for distance, direction in
               zip(edges, ((-1, 0), (0, -1), (1, 0), (0, 1))) if distance <= 240]
    left, top = np.maximum(0, np.floor(center - side * 1.5)).astype(int)
    right, bottom = np.minimum(CLIENT_SIZE, np.ceil(center + side * 1.5)).astype(int)
    hsv = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (85, 100, 140), (118, 255, 255))
    mask |= cv2.inRange(hsv, (0, 0, 215), (179, 70, 255))
    mask[max(0, y1 - top - 3):y2 - top + 3, max(0, x1 - left - 3):x2 - left + 3] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        area = cv2.contourArea(contour)
        if not .012 * side ** 2 <= area <= .2 * side ** 2:
            continue
        polygon = cv2.approxPolyDP(contour, .07 * cv2.arcLength(contour, True), True).reshape(-1, 2)
        if len(polygon) != 3:
            continue
        polygon = polygon.astype(float) + [left, top]
        mean = polygon.mean(axis=0)
        offset = mean - center
        length = float(np.linalg.norm(offset))
        if not .45 * side < length < 1.5 * side:
            continue
        if not any(offset @ direction > .2 * length for direction in outward):
            continue
        along = polygon @ (offset / length)
        tip = int(np.argmax(along))
        base = np.delete(along, tip)
        if along[tip] - base.max() > .12 * side and abs(base[1] - base[0]) < .3 * side:
            return True
    return False


def story_markers(image):
    """匹配不同大小的蓝色主线菱形，返回客户区 box、position、confidence 和 scene/edge 形态。

    候选按蓝色外框的实际大小归一化，并微调三种尺度匹配；不依赖半自动旧模板及固定点击偏移。
    顶栏、任务栏、小地图与底部按钮不参与识别。离屏点击必须同时满足靠边与外向箭头判据，
    靠边却无箭头的标记保留为 scene，只允许后续验证所属脚圈，不能直接点击。
    """
    if image is None or image.shape != (999, 1776, 3):
        raise ValueError('主线标记识别需要 1776×999 BGR 客户区截图。')
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (88, 130, 140), (115, 255, 255))
    for left, top, right, bottom in HUD_RECTS:
        blue[top:bottom, left:right] = 0
    contours, _ = cv2.findContours(blue, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    template, mask = marker_template()
    result = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if not (28 <= width <= 110 and 28 <= height <= 110 and .8 < width / height < 1.25):
            continue
        if not .3 < cv2.contourArea(contour) / (width * height) < .75:
            continue
        center = np.array([x + width / 2, y + height / 2])
        best = None
        for scale in (.95, 1., 1.05):
            half = np.array([width, height]) * scale / 2
            left, top = np.rint(center - half).astype(int)
            right, bottom = np.rint(center + half).astype(int)
            if not (0 <= left < right <= 1776 and 0 <= top < bottom <= 999):
                continue
            patch = cv2.resize(cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY), (64, 64))
            score = float(cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED, mask=mask)[0, 0])
            if np.isfinite(score) and (best is None or score > best):
                best = score
        if best is None or best < MATCH_THRESHOLD:
            continue
        box = [int(x), int(y), int(x + width), int(y + height)]
        if any(x < right and x + width > left and y < bottom and y + height > top
               for left, top, right, bottom in HUD_RECTS):
            continue
        result.append(dict(kind='edge' if outward_arrow(image, box) else 'scene', box=box,
                           position=center.tolist(), confidence=round(best, 4)))
    selected = []
    for item in sorted(result, key=lambda value: value['confidence'], reverse=True):
        if any(np.linalg.norm(np.asarray(item['position']) - old['position']) < 25 for old in selected):
            continue
        selected.append(item)
    return selected


def marked_ring(markers, centers):
    """仅在一个场内主线标记下有唯一脚圈时返回该脚圈；返回值仍是客户区坐标，不是地图落点。

    按当前标记尺寸限制候选范围，容许视角造成的侧向偏移，不按固定向下偏移猜点击点。
    多个脚圈竞争时拒绝，后续可换视角重识别；本方法不判断道路或小地图敌人身份。
    """
    scenes = [item for item in markers if item['kind'] == 'scene']
    if len(scenes) != 1:
        return None
    marker = scenes[0]
    width = marker['box'][2] - marker['box'][0]
    height = marker['box'][3] - marker['box'][1]
    candidates = [np.asarray(point, float) for point in centers
                  if abs(point[0] - marker['position'][0]) <= 1.3 * width
                  and .8 * height <= point[1] - marker['position'][1] <= 5 * height]
    return candidates[0] if len(candidates) == 1 else None
