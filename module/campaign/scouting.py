"""侦察：有目的地时查当前路段和相关汇合岔路，无目的地时查本分量中可能更近的敌人。

小地图只能看到小队周围一块直线范围，弯路上直线近的敌人路程可能很远，而路程更近的那段路
往往在视野外。展开大地图可以拖到远处，那里的敌人标记照样显示，所以找敌人不必靠走路：
先检查当前候选敌人的实际 A* 路径，再用本分量的近似路程场补查可能更近的支路。
路径仍有未观测格时不能仅凭粗网格估计停止；冷却只限制重试，不是已经看清的证据。

本模块只做纯计算，不产生输入；拖动与配准由会话完成。
"""

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

from module.campaign.goals import nearest_enemy
from module.campaign.map.map_state import GRID
from module.campaign.route_branches import branch_scout_points, route_branches
from module.campaign.route_planning import ROUTE_CORRIDOR, ROUTE_DETOUR, route_path
from module.campaign.run_state import COMBAT_ENEMY_KINDS, OBSERVED_CELL
from module.campaign.subgoals import RoadView

# 一个已观测粗格每边包含的 8 像素路网格数。
CELLS = OBSERVED_CELL // GRID
# 点落到路程场上时向外找本分量网格的半径（网格数）：2 格即 16 像素，与道路归属容差同量级。
SNAP_CELLS = 2


def walking_field(cut, squad):
    """切开图上从小队出发、沿本分量 8 像素网格的路程（地图像素），形状同 cut.regions。

    本分量以外为 inf；小队不在切开图道路上时返回 None。
    相邻格按八邻域连边（斜向按 √2 计），同一分量内的网格才连，所以不会穿过光幕切口。
    网格粗于道路，斜向相邻偶尔会贴着细墙角算近一点；只用于侦察排序与停止判定，
    最终选目标仍由逐候选 A* 给出行走距离。
    """
    home = cut.component(squad)
    if not home:
        return None
    mask = cut.regions == home
    rows, columns = np.nonzero(mask)
    if not len(rows):
        return None
    index = np.full(mask.shape, -1, np.int64)
    index[rows, columns] = np.arange(len(rows))
    height, width = mask.shape
    sources, targets, weights = [], [], []
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        ny, nx = rows + dy, columns + dx
        keep = (ny < height) & (nx >= 0) & (nx < width)
        y0, x0, ny, nx = rows[keep], columns[keep], ny[keep], nx[keep]
        linked = mask[ny, nx]
        sources.append(index[y0[linked], x0[linked]])
        targets.append(index[ny[linked], nx[linked]])
        weights.append(np.full(int(linked.sum()), GRID * np.hypot(dy, dx)))
    count = len(rows)
    graph = coo_matrix((np.concatenate(weights), (np.concatenate(sources), np.concatenate(targets))),
                       shape=(count, count)).tocsr()
    squad = np.asarray(squad, float)
    offsets = np.hypot(columns * GRID - squad[0], rows * GRID - squad[1])
    start = int(np.argmin(offsets))
    distances = dijkstra(graph, directed=False, indices=start)
    field = np.full(mask.shape, np.inf)
    field[rows, columns] = distances + offsets[start]
    return field


def field_at(field, point):
    """点在路程场上的路程：附近 SNAP_CELLS 格内本分量网格的路程加上到该格的直线距离，取最小；附近没有返回 inf。"""
    point = np.asarray(point, float)
    if point.shape != (2,) or not np.isfinite(point).all():
        return float('inf')
    column, row = int(round(point[0] / GRID)), int(round(point[1] / GRID))
    top, left = max(0, row - SNAP_CELLS), max(0, column - SNAP_CELLS)
    window = field[top:row + SNAP_CELLS + 1, left:column + SNAP_CELLS + 1]
    if not window.size:
        return float('inf')
    ys, xs = np.mgrid[top:top + window.shape[0], left:left + window.shape[1]]
    return float(np.min(window + np.hypot(xs * GRID - point[0], ys * GRID - point[1])))


def enemy_hint_focus(state, cut, squad, include_cooling=False, goal=None):
    """选择需要重新观察的历史敌人位置，只返回镜头侦察落点，不执行地面移动。

    无目的地时只查可能比当前已确认敌人更近的线索；有目的地时限于本次执行路线及相关汇合岔路，
    并复查道路绕行，避免历史标注把主线路程引向无关支路。已覆盖网格不能代替敌人复核。
    配准失败的暂不可扫位置沿用现有的小队距离解除规则；冷却只限制重试。
    """
    hints = [item for item in state.enemy_hints() if item['kind'] in COMBAT_ENEMY_KINDS]
    if not hints:
        return None
    target = (goal if goal and goal.get('position') is not None else state.route_goal)
    route = route_path(cut, squad, target['position']) if state.route_goal is not None else None
    if state.route_goal is not None and route is None:
        return None
    sections = route_branches(state, cut, squad, state.route_goal)['sections'] if route is not None else []
    nearest, _ = nearest_enemy(state, cut, squad)
    limit = nearest['distance'] if nearest else float('inf')
    candidates = []
    for item in hints:
        identifier = f'scout-hint-{item["id"]}'
        if state.is_unscoutable(identifier, squad) or (not include_cooling and state.cooling(identifier)):
            continue
        path = route_path(cut, squad, item['position'])
        if path is None:
            continue
        distance = float(path[1][-1])
        if route is not None:
            position = np.asarray(item['position'], float)
            on_branch = any(section.active and section.contains(position) for section in sections)
            if not on_branch:
                if np.linalg.norm(route[0] - position, axis=1).min() > ROUTE_CORRIDOR:
                    continue
                tail = route_path(cut, position, target['position'])
                if tail is None or distance + tail[1][-1] - route[1][-1] > ROUTE_DETOUR:
                    continue
        elif distance >= limit:
            continue
        candidates.append(dict(id=identifier, position=item['position'], distance=round(distance, 1),
                               known=round(limit, 1) if np.isfinite(limit) else None, source='enemy_hint'))
    return min(candidates, key=lambda item: item['distance']) if candidates else None


def scout_focus(state, cut, squad, include_cooling=False, goal=None):
    """挑下一屏侦察落点，优先候选敌人的实际路径，再查可能更近的未知支路。

    每次按最新敌人记录重新选择，新增近敌会缩短侦察范围。路径来自同一切开图的 A*，
    不能用近似路程场代替：粗网格连边可能跨过细墙，实际 A* 会校验连线道路并按净空绕行。
    支路按 32px 格内的最短近似路程排序，截止距离取候选实际路径加两端吸附距离。
    默认跳过冷却格以便继续看其他区域；出发前须 include_cooling=True 复核，
    只有这时返回 None 才代表没有待查区域。所有距离与位置均为底图像素。
    路程模式的 goal 指本段执行落点，查该段及已进入的汇合岔路两侧；省略时查完整目的地路线。
    """
    hint = enemy_hint_focus(state, cut, squad, include_cooling, goal)
    if hint is not None:
        return hint
    if state.route_goal is not None:
        target = goal if goal and goal.get('position') is not None else state.route_goal
        return route_scout_focus(state, cut, squad, target, include_cooling)
    target, _ = nearest_enemy(state, cut, squad)
    known = float('inf')
    if target is not None:
        _, path = RoadView(cut).route(squad, target['position'])
        distances = np.r_[0., np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
        distances += np.linalg.norm(path[0] - np.asarray(squad, float))
        known = float(distances[-1] + np.linalg.norm(path[-1] - np.asarray(target['position'], float)))
        for point, distance in zip(path, distances):
            if state.is_observed(point):
                continue
            column, row = (point // OBSERVED_CELL).astype(int)
            identifier = f'scout-{column}-{row}'
            if state.is_unscoutable(identifier, squad):
                continue
            if not include_cooling and state.cooling(identifier):
                continue
            return dict(id=identifier, position=point.tolist(), distance=round(float(distance), 1),
                        known=round(known, 1), source='target_route')
    field = walking_field(cut, squad)
    if field is None:
        return None
    observed = state.observed
    padded = np.full((observed.shape[0] * CELLS, observed.shape[1] * CELLS), np.inf)
    height, width = min(field.shape[0], padded.shape[0]), min(field.shape[1], padded.shape[1])
    padded[:height, :width] = field[:height, :width]
    blocks = padded.reshape(observed.shape[0], CELLS, observed.shape[1], CELLS)
    nearest = blocks.min(axis=(1, 3))
    rows, columns = np.nonzero(np.isfinite(nearest) & (observed == 0))
    for order in np.argsort(nearest[rows, columns], kind='stable'):
        row, column = int(rows[order]), int(columns[order])
        identifier = f'scout-{column}-{row}'
        if state.is_unscoutable(identifier, squad):
            continue
        if not include_cooling and state.cooling(identifier):
            continue
        frontier = float(nearest[row, column])
        if known <= frontier:
            return None
        block = blocks[row, :, column, :]
        dy, dx = np.unravel_index(int(np.argmin(block)), block.shape)
        position = [float((column * CELLS + dx) * GRID), float((row * CELLS + dy) * GRID)]
        return dict(id=identifier, position=position, distance=round(frontier, 1),
                    known=round(known, 1) if np.isfinite(known) else None, source='nearer_road')
    return None


def route_scout_focus(state, cut, squad, target, include_cooling=False):
    """补查本次执行路段及相关汇合段两侧，不扫描全分量中更近但无关的敌人。

    格子沿用运行状态的覆盖记录；切换执行目标不清空已扫描区域。冷却仅限制重试，
    include_cooling=True 仍返回未看清的格子，保证不能凭失败冷却跳过前置道路。
    """
    route = route_path(cut, squad, target['position'])
    if route is None:
        return None
    path, distances = route
    home = cut.component(squad)
    seen = set()
    for point, distance in zip(path, distances):
        for offset in ((0, 0), (OBSERVED_CELL, 0), (-OBSERVED_CELL, 0),
                       (0, OBSERVED_CELL), (0, -OBSERVED_CELL)):
            candidate = point + offset
            if cut.component(candidate) != home or state.is_observed(candidate):
                continue
            column, row = (candidate // OBSERVED_CELL).astype(int)
            identifier = f'scout-{column}-{row}'
            if identifier in seen:
                continue
            seen.add(identifier)
            if state.is_unscoutable(identifier, squad):
                continue
            if not include_cooling and state.cooling(identifier):
                continue
            return dict(id=identifier, position=candidate.tolist(), distance=round(float(distance), 1),
                        known=round(float(distances[-1]), 1), source='route_road')
    for point in branch_scout_points(state, cut, squad, path):
        if state.is_observed(point):
            continue
        column, row = point // OBSERVED_CELL
        identifier = f'scout-{column}-{row}'
        if state.is_unscoutable(identifier, squad):
            continue
        if not include_cooling and state.cooling(identifier):
            continue
        return dict(id=identifier, position=point.tolist(), distance=round(float(np.linalg.norm(point - squad)), 1),
                    known=None, source='route_branch')
    return None
