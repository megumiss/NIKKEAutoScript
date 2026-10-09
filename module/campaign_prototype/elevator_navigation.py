"""在已验证的平面道路之间规划标注电梯，不把传送边当作步行道路。"""

import heapq

import numpy as np

from .edited_map import road_distance
from .probe import NoConnectedRoad


def walking_distance(localizer, start, target):
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
