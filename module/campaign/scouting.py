"""侦察：小队不动，拖展开大地图按路程由近到远查看本分量，确定按路程最近的敌人。

小地图只能看到小队周围一块直线范围，弯路上直线近的敌人路程可能很远，而路程更近的那段路
往往在视野外。展开大地图可以拖到远处，那里的敌人标记照样显示，所以找敌人不必靠走路：
在切开路网上算一次本分量的路程场，按路程从近到远把还没看过的区域拖进视野。
停止条件是 Dijkstra 式的：已知敌人里路程最短的那只不比最近的未看区域更远，它就一定是
本分量里按路程最近的敌人，再看下去不会出现更近的。

本模块只做纯计算，不产生输入；拖动与配准由会话完成。
"""

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

from module.campaign.map.map_state import GRID
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


def scout_focus(state, cut, squad):
    """下一屏侦察的落点；已知敌人不比最近的未看区域更远、或本分量已看完时返回 None。

    未看区域按 RunState 的 32 像素已观测粗格判断，格的路程取格内本分量网格的最小路程，
    落点取路程最短的那个网格（必在道路上，配准时视野里一定有路）。
    配准连续失败的格由调用方用 `scout-<列>-<行>` 记冷却，这里跳过冷却中的格。
    返回 dict(id, position, distance, known)：known 是当前已知最近敌人的路程，没有为 None。
    """
    field = walking_field(cut, squad)
    if field is None:
        return None
    observed = state.observed
    padded = np.full((observed.shape[0] * CELLS, observed.shape[1] * CELLS), np.inf)
    height, width = min(field.shape[0], padded.shape[0]), min(field.shape[1], padded.shape[1])
    padded[:height, :width] = field[:height, :width]
    blocks = padded.reshape(observed.shape[0], CELLS, observed.shape[1], CELLS)
    nearest = blocks.min(axis=(1, 3))
    known = min((field_at(field, record['position']) for record in state.pending_enemies()
                 if record['kind'] == 'normal'), default=float('inf'))
    rows, columns = np.nonzero(np.isfinite(nearest) & (observed == 0))
    for order in np.argsort(nearest[rows, columns], kind='stable'):
        row, column = int(rows[order]), int(columns[order])
        identifier = f'scout-{column}-{row}'
        if state.cooling(identifier):
            continue
        frontier = float(nearest[row, column])
        if known <= frontier:
            return None
        block = blocks[row, :, column, :]
        dy, dx = np.unravel_index(int(np.argmin(block)), block.shape)
        position = [float((column * CELLS + dx) * GRID), float((row * CELLS + dy) * GRID)]
        return dict(id=identifier, position=position, distance=round(frontier, 1),
                    known=round(known, 1) if np.isfinite(known) else None)
    return None
