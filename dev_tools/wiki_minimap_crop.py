"""Extract cropped Wiki minimaps without assuming a full-screen layout or fixed squad position."""

import cv2
import numpy as np


def cropped_ring(panel, road):
    """要求圆周覆盖和道路支持，并拒绝多个位置的圆环，避免把控件当作小队。"""
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, 145), (179, 130, 255))
    height, width = white.shape
    white[:round(height * .12), :round(width * .18)] = 0
    white[:round(height * .12), -round(width * .18):] = 0
    white[-round(height * .13):, -round(width * .35):] = 0
    circles = cv2.HoughCircles(white, cv2.HOUGH_GRADIENT, 1, 12,
                               param1=100, param2=12, minRadius=8, maxRadius=35)
    if circles is None:
        raise ValueError('No squad ring in cropped minimap.')
    ys, xs = np.nonzero(white)
    points = np.column_stack([xs, ys]).astype(float)
    candidates = []
    for x, y, radius in circles[0]:
        center = np.array([x, y], float)
        for _ in range(3):
            keep = np.abs(np.linalg.norm(points - center, axis=1) - radius) < 1.8
            q = points[keep]
            if len(q) < 25:
                break
            solution = np.linalg.lstsq(np.column_stack([2 * q, np.ones(len(q))]),
                                       (q * q).sum(axis=1), rcond=None)[0]
            center = solution[:2]
            radius = np.sqrt(max(0, solution[2] + (center * center).sum()))
        if not 8 < radius < 35 or len(q) < 25:
            continue
        if not (radius + 2 < center[0] < width - radius - 2
                and radius + 2 < center[1] < height - radius - 2):
            continue
        angles = np.arctan2(q[:, 1] - center[1], q[:, 0] - center[0])
        if len(np.unique(np.floor((angles + np.pi) * 16 / (2 * np.pi)))) < 11:
            continue
        disk = np.zeros(white.shape, np.uint8)
        cv2.circle(disk, tuple(np.rint(center).astype(int)), max(3, round(radius * .7)), 255, -1)
        if np.mean(road[disk > 0] > 0) < .4:
            continue
        candidates.append((len(q), center, float(radius)))
    if not candidates:
        raise ValueError('Cropped minimap ring lacks circular or road support.')
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, center, radius = candidates[0]
    if any(np.linalg.norm(other - center) > 5 for _, other, _ in candidates[1:]):
        raise ValueError('Multiple squad rings in cropped minimap.')
    return center, radius


def cropped_minimap(image):
    """用蓝色面板的矩形覆盖提取候选，等比归一化尺寸后独立验证小队圆环。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (85, 50, 25), (120, 255, 255))
    blue = cv2.morphologyEx(blue, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if min(w, h) < 120:
            continue
        patch = blue[y:y + h, x:x + w] > 0
        columns = np.flatnonzero(patch.mean(axis=0) > .8)
        if len(columns) >= 120:
            x, w = x + int(columns[0]), int(columns[-1] - columns[0] + 1)
        boxes.append((x, y, w, h))
    edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 60, 160)
    outlines, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for outline in outlines:
        polygon = cv2.approxPolyDP(outline, .02 * cv2.arcLength(outline, True), True)
        if len(polygon) == 4:
            x, y, w, h = cv2.boundingRect(polygon)
            if cv2.contourArea(polygon) > .9 * w * h:
                boxes.append((x, y, w, h))
    results = []
    for x, y, w, h in set(boxes):
        if min(w, h) < 120 or not .65 <= w / h <= 1.6:
            continue
        if np.mean(blue[y:y + h, x:x + w] > 0) < .75:
            continue
        scale = 300 / max(w, h)
        size = (round(w * scale), round(h * scale))
        panel = cv2.resize(image[y:y + h, x:x + w], size)
        colors = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
        road = cv2.inRange(colors, (85, 65, 115), (115, 255, 255))
        road = cv2.morphologyEx(road, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        try:
            player, radius = cropped_ring(panel, road)
        except ValueError:
            continue
        valid = np.full(road.shape, 255, np.uint8)
        valid[:3] = valid[-3:] = valid[:, :3] = valid[:, -3:] = 0
        bright = ((colors[:, :, 1] < 130) & (colors[:, :, 2] > 145)).astype(np.uint8) * 255
        red = cv2.inRange(colors, (0, 50, 130), (12, 255, 255))
        red |= cv2.inRange(colors, (150, 50, 130), (179, 255, 255))
        valid[cv2.dilate(bright | red, np.ones((7, 7), np.uint8)) > 0] = 0
        cv2.circle(valid, tuple(np.rint(player).astype(int)), round(radius + 5), 0, -1)
        road[valid == 0] = 0
        if np.count_nonzero(road) < 200:
            continue
        results.append((panel, road, valid, player, (x, y, x + w, y + h)))
    results.sort(key=lambda r: (r[4][2] - r[4][0]) * (r[4][3] - r[4][1]), reverse=True)
    centers = [np.array(r[4][:2]) + r[3] * np.array([r[4][2] - r[4][0], r[4][3] - r[4][1]])
               / np.array(r[0].shape[1::-1]) for r in results]
    if not results or any(np.linalg.norm(center - centers[0]) > 5 for center in centers[1:]):
        raise ValueError('Expected one supported cropped minimap and an unambiguous squad ring.')
    return results[0]
