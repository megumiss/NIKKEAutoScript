"""共享道路 A*：只计算路径，不加载地图包或取得游戏窗口。"""

import heapq

import cv2
import numpy as np

from module.campaign.map.geometry import jacobian
from module.campaign.navigation import controls as goto

MIN_REGION_NODES = 4


def traversable_grid(grid, minimum=MIN_REGION_NODES, threshold=0):
    """丢弃不超过 minimum 个格子的孤立分区，返回可走网格。

    这些分区是细路采样偶然留下的少量格子，不是可去的地方。threshold 只用于取连通块
    （v3 无净空门槛，传 0）；所有分区都退化时按原样返回，宁可规划得差也不要无路可走。
    分组用八邻域，与路由图一致：按四邻域分组会把细斜路上的采样链逐格判成碎片后整条抹掉。
    """
    mask = (grid > threshold).astype(np.uint8)
    count, components = cv2.connectedComponents(mask, connectivity=8)
    if count <= 1:
        return grid
    sizes = np.bincount(components.ravel())
    degenerate = np.isin(components, np.flatnonzero(sizes[1:] <= minimum) + 1)
    if not degenerate.any() or not (mask.astype(bool) & ~degenerate).any():
        return grid
    grid = grid.copy()
    grid[degenerate] = 0
    return grid


def edge_tables(road, shape, step=8):
    """四个方向的边表：两格之间 step+1 个连线像素全部是路才有边。

    用线状结构元素腐蚀道路掩码，再在连线中点采样：腐蚀结果为真等价于核覆盖的整条连线都是路。
    这样细斜路上没有正交邻格也能连通，同时不会切过墙角，也不会跨过比采样间距更窄的墙。
    每张表与采样网格同形，索引取两格坐标的逐分量最小值（即 2×2 块的左上角）。
    地图外按非道路处理，否则边沿的边会凭复制边界成立。
    """
    half = step // 2
    diagonal = np.eye(step + 1, dtype=np.uint8)
    specs = {
        'horizontal': (np.ones((1, step + 1), np.uint8), (half, 0)),
        'vertical': (np.ones((step + 1, 1), np.uint8), (0, half)),
        'diag_dr': (diagonal, (half, half)),
        'diag_ur': (np.fliplr(diagonal), (half, half)),
    }
    tables = {}
    for name, (kernel, (ox, oy)) in specs.items():
        eroded = cv2.erode(road, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
        padded = np.pad(eroded, ((0, step), (0, step)))
        tables[name] = padded[oy::step, ox::step][: shape[0], : shape[1]].astype(bool)
    return tables


class NoConnectedRoad(RuntimeError):
    """两点各自可定位到道路，但道路网格之间不连通。"""


def grid_route(road, labels, start, target, step):
    """在给定采样间距上搜索一条路径；分区预检不通过抛 NoConnectedRoad，搜索失败返回 None。

    road 为二值道路掩码，labels 为 8px 网格上的 effective 分区（无分区时为 None），
    start、target 使用地图像素。返回值与 Localizer.route 一致，调用方按采样级别重试。
    """
    clearance = cv2.distanceTransform(road.astype(np.uint8), cv2.DIST_L2, 5)
    grid = clearance[::step, ::step]
    road = road.astype(np.uint8)
    label_grid = None
    blocked = np.zeros(grid.shape, bool)
    if labels is not None:
        scale = 8 // step
        label_grid = (np.repeat(np.repeat(labels, scale, axis=0), scale, axis=1) if scale > 1 else labels)[
            : grid.shape[0], : grid.shape[1]
        ]
        blocked = (grid > 0) & (label_grid == 0)
        grid = grid.copy()
        grid[label_grid == 0] = 0
    grid = traversable_grid(grid, minimum=MIN_REGION_NODES, threshold=0)
    ys, xs = np.nonzero(grid > 0)
    nodes = np.column_stack([xs, ys])
    if not len(nodes):
        raise RuntimeError('No traversable road nodes')
    nearest = lambda point: tuple(nodes[np.argmin(np.linalg.norm(nodes * step - point, axis=1))])
    source, goal = nearest(start), nearest(target)
    # 可达性以 effective 分区为准：v3 的分区算在全分辨率道路掩码上并已施加人工切口，
    # 比采样网格上的搜索更权威；搜索失败只说明这一级采样画不出路径。
    if label_grid is not None and label_grid[source[1], source[0]] != label_grid[goal[1], goal[0]]:
        raise NoConnectedRoad('No connected road route')
    edges = edge_tables(road, grid.shape, step)
    queue = [(0.0, source)]
    costs, parents = {source: 0.0}, {}
    while queue:
        _, current = heapq.heappop(queue)
        if current == goal:
            break
        x0, y0 = current
        # 边表索引取两格坐标的逐分量最小值，向左、向上走与邻居为起点的边是同一条。
        for dx, dy, edge in (
            (1, 0, edges['horizontal']),
            (-1, 0, edges['horizontal']),
            (0, 1, edges['vertical']),
            (0, -1, edges['vertical']),
            (1, 1, edges['diag_dr']),
            (-1, -1, edges['diag_dr']),
            (1, -1, edges['diag_ur']),
            (-1, 1, edges['diag_ur']),
        ):
            x, y = x0 + dx, y0 + dy
            if not (0 <= x < grid.shape[1] and 0 <= y < grid.shape[0]) or grid[y, x] <= 0:
                continue
            if not edge[min(y0, y), min(x0, x)]:
                continue
            cost = costs[current] + np.hypot(dx, dy) * (1 + 8 / grid[y, x])
            node = (x, y)
            if cost >= costs.get(node, float('inf')):
                continue
            costs[node], parents[node] = cost, current
            heapq.heappush(queue, (cost + np.hypot(x - goal[0], y - goal[1]), node))
    if goal not in costs:
        return None
    path, node = [goal], goal
    while node != source:
        node = parents[node]
        path.append(node)
    path = np.asarray(path[::-1], float) * step
    waypoint = path[0]
    for candidate in [*path, target]:
        line = np.rint(np.linspace(start, candidate, max(2, int(np.linalg.norm(candidate - start))))).astype(int)
        if (
            np.linalg.norm(candidate - start) > 100
            or np.any(clearance[line[:, 1], line[:, 0]] < 0.5)
            or np.any(blocked[line[:, 1] // step, line[:, 0] // step])
        ):
            break
        waypoint = candidate
    return waypoint, path

    def click(self, position, player_roi, waypoint, anchor):
        """比较新旧投影的局部 Jacobian，把地图短位移换回已有场景点击标定。

        position/waypoint 为地图坐标，player_roi 为小队 ROI 坐标，anchor 为客户区地面点。
        在小队附近比较两套投影的局部尺度，再由 movement_click 限制短移幅度；远点应使用完整镜头规划。
        """
        old_to_new = jacobian(self.matrix, np.asarray(player_roi)) @ np.linalg.inv(
            jacobian(self.old_matrix, np.asarray(player_roi))
        )
        offset = np.linalg.solve(old_to_new, waypoint - position)
        return goto.movement_click(anchor, offset)


class RoadLocalizer:
    """对已有 road 与 route_labels 提供共享道路寻路，不负责实验数据初始化。"""

    def route(self, start, target):
        """在道路距离场上运行带净空代价的 A*，边按全分辨率道路像素验证，再选择局部可直达路点。

        start、target 使用地图像素。连通性由 effective 分区（v3 起在全分辨率道路掩码上预算，
        并已施加人工切口）预检，节点不设净空硬门槛（硬门槛会在细路上造出单格孤岛和假分裂，
        ch38 收集品 02），净空偏好由边代价承担。
        先按 8px 采样，画不出路径再按 4px 重试一次：细路瓶颈实测约 4px 宽（clearance 1.99），
        8px 采样跨不过去，而分区已经确认两点连通，这时判不可达是错的。
        返回局部可直达 waypoint 和完整网格路径；两级都画不出时才失败，二维连通不证明跨层可通行。
        """
        start, target = np.asarray(start, float), np.asarray(target, float)
        if start.shape != (2,) or target.shape != (2,) or not np.isfinite([*start, *target]).all():
            raise ValueError('Nonfinite route coordinate')
        for point in (start, target):
            if np.any(np.asarray(point) < 0) or np.any(np.asarray(point) >= self.road.shape[::-1]):
                raise ValueError('Route coordinate outside map')
        labels = getattr(self, 'route_labels', None)
        for step in (8, 4):
            found = grid_route(self.road, labels, start, target, step)
            if found is not None:
                return found
        raise NoConnectedRoad('No connected road route')
