"""YOLO observations adapted to the prototype's existing image and map coordinates."""

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from module.campaign.vision.detection import detect_minimap, detect_scene


def crop_candidate(image, detection, margin=4):
    """裁出检测框周围的小图，并返回它在原图里的左上偏移。

    margin 给精定位留出余量，框本身可能把符号边缘切掉一两像素；裁剪范围被夹回图内，所以贴边的
    候选实际余量会小于 margin。返回的偏移必须加回精定位结果，否则坐标仍停留在局部图上。
    """
    box = np.asarray(detection.box)
    left, top = np.maximum(0, np.floor(box[:2] - margin)).astype(int)
    right, bottom = np.minimum(image.shape[1::-1], np.ceil(box[2:] + margin)).astype(int)
    return image[top:bottom, left:right], np.array([left, top])


@lru_cache(maxsize=1)
def arrow_template():
    """小队箭头的灰度模板，进程内只读一次。

    模板中心就是动画标定采用的箭头落点基准，换图等于改变标定，必须连带重新标定。
    """
    path = Path(__file__).parent / 'assets/squad_arrow_tpl.png'
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(path)
    return template


def refine_arrow(image, candidate):
    """在 YOLO 候选框内做模板匹配，给出箭头的精确中心；不可信时返回 None。

    只在候选框内精定位，是为了保留动画标定使用的「模板中心」这一基准——整幅画面匹配会落在
    别的高亮处，等于悄悄换掉标定原点。
    箭头随呼吸动画变色变亮，所以用三组白/黄掩码分别匹配，取分数最高的一组；尺度要扫一遍是因为
    镜头缩放会让箭头大小变化。
    结果偏离候选中心超过 12 像素或框最大边的三分之一就丢弃（匹配到了相邻符号），
    最终分数低于 0.65 也返回 None，宁可这帧不定位也不要给出错误落点。
    """
    patch, offset = crop_candidate(image, candidate, margin=14)
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    masks = [cv2.inRange(hsv, (0, 0, 215), (179, 110, 255)),
             cv2.inRange(hsv, (0, 0, 245), (179, 80, 255)),
             cv2.inRange(hsv, (5, 160, 190), (30, 255, 255))]
    best = None
    for scale in (.6, .65, .7, .75, .8, .85, .9, .95, 1., 1.05, 1.1, 1.15, 1.2):
        template = cv2.resize(arrow_template(), None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        if template.shape[0] > patch.shape[0] or template.shape[1] > patch.shape[1]:
            continue
        for mask in masks:
            scores = cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, point = cv2.minMaxLoc(scores)
            center = offset + point + np.array(template.shape[::-1]) / 2
            largest_side = np.max(np.ptp(np.reshape(candidate.box, (2, 2)), axis=0))
            if np.linalg.norm(center - candidate.center) > max(12., largest_side / 3):
                continue
            if best is None or score > best[0]:
                best = score, center
    return best[1] if best is not None and best[0] >= .65 else None


def squad_arrow(image):
    """客户区里小队箭头的精确中心；不是恰好一个可信箭头时返回 None。

    要求精定位后只剩一个结果：出现多个说明画面里有别的相似符号，这时无法确定哪个是小队，
    继续用会把移动指令发到错误位置。
    """
    candidates = [item for item in detect_scene(image) if item.label == 'scene_squad_arrow']
    centers = [refine_arrow(image, item) for item in candidates]
    centers = [center for center in centers if center is not None]
    if len(centers) != 1:
        return None
    return centers[0]


def collectible_indicator(image):
    """画面里的收集品提示；没有则返回 None。

    提示同时出现多个时只取置信度最高的那个：它只用来判断「附近有收集品」，不需要全部列出。
    返回的 position 是检测框中心，没有做精定位，不能直接当点击落点用。
    """
    candidates = [item for item in detect_scene(image) if item.label == 'scene_collectible_indicator']
    if not candidates:
        return None
    item = max(candidates, key=lambda candidate: candidate.confidence)
    return dict(kind='collectible_indicator', confidence=item.confidence,
                position=item.center.tolist(), detector='yolo', box=list(item.box))


def refine_ring(image, candidate):
    """在 YOLO 圆环框内拟合实际椭圆，返回圆心；证据不足时返回 None。

    圆环被透视压扁，所以拟合椭圆而不是圆。环是半透明的，会把下方道路的青色透上来（紧凑地图
    尤其明显），因此先取低饱和的白色，再把红色敌人标记连同膨胀一圈剔除，避免重叠时拟合被拽偏。
    虚线动画会让部分环段达不到白色阈值，所以用角度覆盖判断完整性：十六个角度扇区至少命中十一个，
    且 85% 分位的归一化半径偏差不超过 0.3，才认为拟合到的是整个环而不是一段弧。
    圆心必须落在候选框内，否则说明拟合跑到了相邻符号上。
    """
    patch, offset = crop_candidate(image, candidate)
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    # Translucent rings inherit cyan from the road, especially in the compact map.
    white = cv2.inRange(hsv, (0, 0, 145), (179, 145, 255))
    red = cv2.inRange(hsv, (135, 45, 80), (179, 255, 255))
    white[cv2.dilate(red, np.ones((3, 3), np.uint8)) > 0] = 0
    ys, xs = np.nonzero(white)
    if len(xs) < 20:
        return None
    points = np.column_stack((xs, ys)).astype(np.float32)
    center, axes, angle = cv2.fitEllipse(points)
    axes = np.asarray(axes)
    if not np.isfinite([*center, *axes, angle]).all() or min(axes) < 6:
        return None
    radians = np.deg2rad(angle)
    rotation = np.array([[np.cos(radians), np.sin(radians)], [-np.sin(radians), np.cos(radians)]])
    normalized = (points - center) @ rotation.T / (axes / 2)
    angles = np.arctan2(normalized[:, 1], normalized[:, 0])
    coverage = len(np.unique(np.floor((angles + np.pi) * 8 / np.pi)))
    # The dashed animation can leave five of sixteen angular sectors below the white threshold.
    if coverage < 11 or np.quantile(np.abs(np.linalg.norm(normalized, axis=1) - 1), .85) > .3:
        return None
    center = np.asarray(center) + offset
    if np.any(center < candidate.box[:2]) or np.any(center > candidate.box[2:]):
        return None
    return center


def project_centers(points, matrix):
    """把面板像素坐标按单应投到地图坐标，结果保留一位小数。

    matrix 必须是有限的 3×3 单应。齐次分量接近 0 说明点投到了透视地平线上，这种结果没有意义，
    直接报错而不是返回一个巨大的坐标；空输入返回空列表，方便调用方不做特判。
    """
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError('Expected a finite 3x3 map projection.')
    if not points:
        return []
    points = np.asarray(points, dtype=float)
    homogeneous = np.column_stack((points, np.ones(len(points)))) @ matrix.T
    if np.any(np.abs(homogeneous[:, 2]) < 1e-8):
        raise ValueError('Marker projection reaches the perspective horizon.')
    projected = homogeneous[:, :2] / homogeneous[:, 2:]
    if not np.isfinite(projected).all():
        raise ValueError('Marker projection is not finite.')
    return projected.round(1).tolist()


def marker_candidates(image):
    """小地图面板里可用的标记检测，已排掉贴边与界面控件上的误检。

    贴着面板边缘的框是被裁断的符号，中心会偏，一律丢弃。
    右下角是面板自带的图例、紧凑面板左上与右上是缩放与关闭控件，这些位置的「标记」都是界面元素；
    按面板宽度区分展开与紧凑，只有紧凑面板才排顶部两角。
    """
    height, width = image.shape[:2]
    result = []
    for item in detect_minimap(image):
        left, top, right, bottom = item.box
        if left <= 3 or top <= 3 or right >= width - 3 or bottom >= height - 3:
            continue
        if item.center[0] >= width - 75 and item.center[1] >= height - 31:
            continue
        if width < 300 and top < 29 and (left < 42 or right > width - 34):
            continue
        result.append(item)
    return result


def detect_markers(image, matrix):
    """小地图上的小队与敌人，按 matrix 投到地图坐标，返回（小队, 敌人）两组。

    小队必须通过圆环拟合，拟合失败就不计入——定位基准宁缺毋滥；敌人只取检测框中心，
    用于计数和粗略方位，精确落点另见 normal_enemy_markers。
    """
    candidates = marker_candidates(image)
    players, enemies = [], []
    for item in candidates:
        if item.label == 'minimap_squad_ring':
            center = refine_ring(image, item)
            if center is not None:
                players.append(center)
        elif item.label in ('minimap_enemy_normal', 'minimap_enemy_ex'):
            enemies.append(item.center)
    return project_centers(players, matrix), project_centers(enemies, matrix)


def normal_enemy_markers(image, matrix):
    """普通敌人标记的精确位置，已排除 EX 弹窗带出的误检。

    EX 关卡的弹窗里也有一个普通敌人样式的小标记，所以与任一 EX 框有重叠的普通候选都丢掉。
    落点取红色像素凸包的重心而不是检测框中心：标记是水滴形，框中心会偏向尖端，
    重心更贴近它在地图上的实际位置。红色跨越色相环两端，要分两段取。
    """
    candidates = marker_candidates(image)
    normals = [item for item in candidates if item.label == 'minimap_enemy_normal']
    excluded = [item for item in candidates if item.label == 'minimap_enemy_ex']
    centers = []
    for item in normals:
        box = np.asarray(item.box)
        if any(np.all(np.minimum(box[2:], other.box[2:]) > np.maximum(box[:2], other.box[:2]))
               for other in excluded):
            continue
        patch, offset = crop_candidate(image, item, margin=0)
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        red = cv2.inRange(hsv, (135, 45, 80), (179, 255, 255))
        red |= cv2.inRange(hsv, (0, 60, 140), (8, 255, 255))
        ys, xs = np.nonzero(red)
        if len(xs) < 6:
            continue
        moments = cv2.moments(cv2.convexHull(np.column_stack((xs, ys)).astype(np.float32)))
        if moments['m00'] <= 0:
            continue
        centers.append(offset + [moments['m10'] / moments['m00'], moments['m01'] / moments['m00']])
    return project_centers(centers, matrix)


def minimap_masks(image):
    """紧凑小地图的面板、道路掩码、有效域与小队圆心，供底图配准使用。

    小队完全由 YOLO 加圆环拟合给出，同时沿用原有的紧凑道路停止判据；圆环不是恰好一个就报错，
    定位基准不允许歧义。
    面板要先把客户区缩放到 1920×1080 再裁，固定 ROI 是按该尺寸标定的。
    有效域排掉边框和三处界面控件；高亮区域会被膨胀后剔除（光效会被当成道路），但剔除前先在
    亮度图上把圆环周围挖掉，否则环自身的白色会连带把身边的道路一起排掉。
    最后再把圆环本体从有效域里扣掉，并要求剩余道路至少 200 像素，低于此量配准必然不可靠。
    """
    if image.shape != (999, 1776, 3):
        raise ValueError('Expected a 1776x999 client frame for compact-map observation.')
    panel = cv2.resize(image, (1920, 1080))[96:307, 25:243]
    players, _ = detect_markers(panel, np.eye(3))
    if len(players) != 1:
        raise ValueError(f'Expected one compact-map squad ring, got {len(players)}.')
    player = np.asarray(players[0])
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    road = cv2.inRange(hsv, (85, 65, 115), (115, 255, 255))
    road = cv2.morphologyEx(road, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    valid = np.full(road.shape, 255, np.uint8)
    valid[:3] = valid[-3:] = 0
    valid[:, :3] = valid[:, -3:] = 0
    valid[:29, :42] = valid[:29, -34:] = valid[-31:, -76:] = 0
    bright = (hsv[:, :, 2] > 225).astype(np.uint8) * 255
    cv2.circle(bright, tuple(np.rint(player).astype(int)), 29, 0, -1)
    valid[cv2.dilate(bright, np.ones((15, 15), np.uint8)) > 0] = 0
    cv2.circle(valid, tuple(np.rint(player).astype(int)), 21, 0, -1)
    road[valid == 0] = 0
    if np.count_nonzero(road) < 200:
        raise ValueError('Too little minimap road evidence.')
    return panel, road, valid, player


def compact_road_masks(image):
    """紧凑小地图的道路与有效域，不要求小队圆环；供视野中心定位使用。

    提取规则与 minimap_masks 一致，只去掉圆环周边的遮罩；圆环存在时靠亮度过滤吸收。
    """
    if image.shape != (999, 1776, 3):
        raise ValueError('Expected a 1776x999 client frame for compact-map observation.')
    panel = cv2.resize(image, (1920, 1080))[96:307, 25:243]
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    road = cv2.inRange(hsv, (85, 65, 115), (115, 255, 255))
    road = cv2.morphologyEx(road, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    valid = np.full(road.shape, 255, np.uint8)
    valid[:3] = valid[-3:] = 0
    valid[:, :3] = valid[:, -3:] = 0
    valid[:29, :42] = valid[:29, -34:] = valid[-31:, -76:] = 0
    bright = (hsv[:, :, 2] > 225).astype(np.uint8) * 255
    valid[cv2.dilate(bright, np.ones((15, 15), np.uint8)) > 0] = 0
    road[valid == 0] = 0
    if np.count_nonzero(road) < 200:
        raise ValueError('Too little minimap road evidence.')
    return panel, road, valid
