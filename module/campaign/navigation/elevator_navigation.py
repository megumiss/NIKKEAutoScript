"""在已验证的平面道路之间规划标注电梯，不把传送边当作步行道路。"""

import heapq

import cv2
import numpy as np

from module.campaign.map.edited_map import road_distance
from module.campaign.navigation.pathfinding import NoConnectedRoad


def visible_elevator(field, state):
    """只接受当前画面中唯一的指定形态升降台，返回客户区中心；远近由调用方校验。"""
    from module.campaign.vision.detection import detect_scene

    candidates = [item.center for item in detect_scene(field, (f'scene_elevator_{state}',))
                  if item.confidence >= .75]
    return candidates[0] if len(candidates) == 1 else None


def visible_street_elevator(field):
    """在升起的平台下识别地面入口的蓝色箭头和白色底板。

    搜索窗口相对于当前帧识别的上层平台，使用 1776×999 客户区像素；遮挡或候选分散时
    不推测入口。箭头有时被门框分割，允许合并同一块底板上的近邻轮廓。
    """
    lift = visible_elevator(field, 'end')
    if lift is None:
        return None
    x, y = np.rint(lift).astype(int)
    left, top, right, bottom = (max(0, x + 22), max(0, y + 242),
                                min(field.shape[1], x + 146), min(field.shape[0], y + 385))
    if left >= right or top >= bottom:
        return None
    hsv = cv2.cvtColor(field[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (89, 25, 150), (123, 142, 255))
    white = cv2.inRange(hsv, (0, 0, 174), (179, 44, 255))
    joined = cv2.morphologyEx(blue, cv2.MORPH_CLOSE, np.ones((5, 3), np.uint8))
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        i, j, width, height = cv2.boundingRect(contour)
        if not (7 <= width <= 55 and 4 <= height <= 45 and cv2.contourArea(contour) >= 15):
            continue
        bottom_plate = white[max(0, j - 5):j + height + 16, max(0, i - 5):i + width + 5]
        if np.mean(bottom_plate > 0) >= .57:
            candidates.append([left + i + width / 2, top + j + height / 2])
    if not candidates:
        return None
    points = np.asarray(candidates)
    if np.max(np.linalg.norm(points[:, None] - points, axis=2)) > 20.:
        return None
    return np.mean(points, axis=0) + [0., 9.]


def walking_distance(localizer, start, target):
    """两点间沿道路的步行距离；不连通时返回无穷大。

    距离按 A* 路径的实际折线长度算，不是直线距离，这样绕行代价才能和电梯中转比较。
    不连通是正常结果而不是错误，所以 NoConnectedRoad 在这里被吞掉换成无穷大，
    让上层直接用「是否有限」判断能否步行到达。
    """
    try:
        _, path = localizer.route(np.asarray(start, float), np.asarray(target, float))
    except NoConnectedRoad:
        return float('inf')
    return float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())


def plan_elevators(localizer, annotations, start, target):
    """优先普通步行；不连通时在道路段和有向电梯边组成的图上寻找中转路线。

    annotations 必须来自已校验的运行快照。仅消费明确分类的点状电梯与传送关系；
    多边形没有唯一点击位置，不能自动取中心代替入口。返回按顺序执行的传送段。
    """
    if np.isfinite(walking_distance(localizer, start, target)):
        return []
    objects = {item['id']: item for item in annotations['objects']}
    points = [np.asarray(start, float), np.asarray(target, float)]
    indices, transfers = {}, []
    for connection in annotations.get('connections', []):
        if connection.get('category') != 'elevator_connection':
            continue
        ends = [objects[connection[end]] for end in ('from', 'to')]
        if any(item.get('category') != 'ground_elevator' or item['type'] != 'point' for item in ends):
            continue
        if any(road_distance(localizer.road.astype(np.uint8), item['points'][0]) > 12 for item in ends):
            continue
        for item in ends:
            if item['id'] not in indices:
                indices[item['id']] = len(points)
                points.append(np.asarray(item['points'][0], float))
        source, destination = (indices[item['id']] for item in ends)
        transfers.append((source, destination, connection['id']))
        if not connection['directed']:
            transfers.append((destination, source, connection['id']))

    graph = [[] for _ in points]
    for i, first in enumerate(points):
        for j in range(i + 1, len(points)):
            distance = walking_distance(localizer, first, points[j])
            if np.isfinite(distance):
                graph[i].append((j, distance, None))
                graph[j].append((i, distance, None))
    for source, destination, identifier in transfers:
        # 同一道路区域无需传送，且不能仅凭接近出口证明电梯已执行。
        if any(node == destination for node, _, _ in graph[source]):
            continue
        leg = dict(connection=identifier, entry=points[source].tolist(), exit=points[destination].tolist())
        graph[source].append((destination, 1., leg))

    queue, costs, parents = [(0., 0)], {0: 0.}, {}
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != costs[node]:
            continue
        if node == 1:
            legs = []
            while node:
                node, leg = parents[node]
                if leg is not None:
                    legs.append(leg)
            return legs[::-1]
        for neighbor, distance, leg in graph[node]:
            candidate = cost + distance
            if candidate >= costs.get(neighbor, float('inf')):
                continue
            costs[neighbor], parents[neighbor] = candidate, (node, leg)
            heapq.heappush(queue, (candidate, neighbor))
    raise NoConnectedRoad('道路不连通，且没有可用的电梯传送路线；请检查电梯点位和连接方向。')


def at_elevator_exit(localizer, position, leg):
    """出口需属于预期道路区域；容许游戏自动离开平台后的落点偏移。

    第 48 章实测落地后小队停在出口标注约 63px 外，采用 96 地图像素的上限，
    并同时要求入口不可步行到达、出口可步行到达，避免邻近但尚未传送时误报成功。
    """
    if np.isfinite(walking_distance(localizer, position, leg['entry'])):
        return False
    if (np.linalg.norm(np.asarray(position) - leg['exit']) <= 96
            and np.isfinite(walking_distance(localizer, position, leg['exit']))):
        return True
    raise RuntimeError('小队离开入口道路，但未在预期电梯出口区域定位，停止移动。')
