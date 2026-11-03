"""选定目标后检查小队到目标的实际道路；没有目标时才搜索未知道路。

敌人复核使用排除面板边缘的安全覆盖，沿途历史敌人必须重新确认，不能凭道路扫描记录跳过。
本模块只做纯计算，不产生输入；拖动与配准由会话完成。
"""

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

from module.campaign.goals import nearest_enemy
from module.campaign.map.map_state import GRID
from module.campaign.route_planning import route_enemy, route_enemy_candidates, route_path
from module.campaign.run_state import OBSERVED_CELL

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


def scout_focus(state, cut, squad, include_cooling=False, goal=None):
    """选定目标后只复核其前置道路，没有目标时才搜索本分量的未知区域。

    检查范围以传入的执行目标为准，不在侦察中另选全局近敌或扫描无关支路。
    沿途重新检出近敌后缩短本次检查路径；冷却只延迟重试，include_cooling=True 供出发门禁复核。
    所有位置与路程均使用底图像素。
    """
    target = goal if goal and goal.get('position') is not None else state.route_goal
    if target is None:
        target, _ = nearest_enemy(state, cut, squad)
    if target is not None:
        return route_scout_focus(state, cut, squad, target, include_cooling)
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
        block = blocks[row, :, column, :]
        dy, dx = np.unravel_index(int(np.argmin(block)), block.shape)
        position = [float((column * CELLS + dx) * GRID), float((row * CELLS + dy) * GRID)]
        return dict(id=identifier, position=position, distance=round(frontier, 1),
                    known=None, source='nearer_road')
    return None


def route_scout_focus(state, cut, squad, target, include_cooling=False):
    """沿已选目标的实际道路检查遗漏，先处理前置敌人，不扩大到无关支路。

    未重新确认的沿途普通敌人和分类冲突线索独立生成复核落点，即使所在道路已经扫描也不能跳过。
    目标本身就是敌人线索时也检查终点，不因该位置的道路已覆盖而放行。
    未知道路使用安全敌人覆盖而非面板全部覆盖；已确认近敌会缩短路径，避免先扫越过它的远端。
    include_cooling=True 保留冷却中的疑点供执行门禁阻止出发，历史线索不直接转为战斗目标。
    """
    route = route_path(cut, squad, target['position'])
    if route is None:
        return None
    path, distances = route
    enemy = route_enemy(state, cut, squad, target['position'], path, distances)
    if (enemy is not None and enemy['kind'] == 'enemy' and enemy['id'] != target['id']
            and enemy['distance'] < distances[-1]):
        target = enemy
        route = route_path(cut, squad, target['position'])
        if route is None:
            return None
        path, distances = route
    hints = [record for record in state.enemy_hints()
             if (record['kind'] == 'normal' or record.get('classification_ambiguous', False))
             and record['id'] != target['id']]
    hint_focus = None
    for distance, record in route_enemy_candidates(cut, squad, target['position'], path, distances, hints):
        identifier = f"scout-{record['id']}"
        if ((distance >= distances[-1] and record['id'] != target.get('for_enemy'))
                or (not include_cooling and state.cooling(identifier))):
            continue
        hint_focus = dict(id=identifier, enemy_id=record['id'], position=list(record['position']),
                          distance=round(distance, 1), known=round(float(distances[-1]), 1),
                          source='route_enemy', check_enemies=True)
        break
    home = cut.component(squad)
    seen = set()
    for point, distance in zip(path, distances):
        if hint_focus is not None and distance >= hint_focus['distance']:
            return hint_focus
        for offset in ((0, 0), (OBSERVED_CELL, 0), (-OBSERVED_CELL, 0),
                       (0, OBSERVED_CELL), (0, -OBSERVED_CELL)):
            candidate = point + offset
            if cut.component(candidate) != home or state.is_enemy_observed(candidate):
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
                        known=round(float(distances[-1]), 1), source='route_road', check_enemies=True)
    return hint_focus
