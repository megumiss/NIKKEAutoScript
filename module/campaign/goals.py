"""推图每一拍的目标选择：从运行状态与静态地图里挑出下一个该去的点。

纯函数层，不碰设备也不点击：输入是 `RunState`、章节 `StaticMap` 与本帧快照，输出一个目标描述，
执行与否由调用方决定。这样同一套决策可以拿现场帧离线回放校验。
本期实现三条优先级：最近的 pending 敌人 → 前沿探索 → 无进展。光幕封锁尚未进路网
（派生切开路网是下一期），所以这里只按原始路网判可达；**在封锁进路网之前不要让它驱动真实移动**，
否则规划会把小队送去撞光幕。电梯航段同理，等标注与失败回写接上再加。
"""

import cv2
import numpy as np

from module.campaign.navigation import walking_distance
from module.campaign.run_state import OBSERVED_CELL

# 逐候选的行走距离要各自重算距离场与边表（单次 150~350ms），所以先按直线距离排序，
# 只对最近的若干个算行走距离。直线最近不等于行走最近，这是为把一次决策压在一秒上下的取舍。
PLAN_CANDIDATES = 4
# 前沿格到小队的直线距离上限：太远的未观测区先不管，等走近了自然会进候选。
FRONTIER_LIMIT = 900.


def cell_coverage(mask):
    """把全分辨率掩码降到已观测粗网格的形状，格内有任一像素即算覆盖。

    用「格内取最大」而不是缩放插值：插值会把细路整格抹掉，而细路恰恰是前沿探索要去的地方。
    """
    height, width = mask.shape
    grid = (int(np.ceil(height / OBSERVED_CELL)), int(np.ceil(width / OBSERVED_CELL)))
    padded = np.zeros((grid[0] * OBSERVED_CELL, grid[1] * OBSERVED_CELL), mask.dtype)
    padded[:height, :width] = mask
    blocks = padded.reshape(grid[0], OBSERVED_CELL, grid[1], OBSERVED_CELL)
    return blocks.max(axis=(1, 3))


def nearest_enemy(state, chart, squad, key=None):
    """可达的 pending 敌人里行走距离最近的那个；都不可达时返回 None 与被否决的候选。

    先按分量筛掉跨区的候选（分量判定在原始坐标上做，网格吸附会把相邻分量并到一起虚报可达），
    再按直线距离取前 PLAN_CANDIDATES 个算行走距离。返回 (目标, 否决记录)。
    """
    squad = np.asarray(squad, float)
    home = chart.component(squad, key)
    rejected = []
    candidates = []
    for record in state.pending_enemies():
        position = np.asarray(record['position'], float)
        if not home or chart.component(position, key) != home:
            rejected.append({'id': record['id'], 'reason': 'different_component'})
            continue
        candidates.append((float(np.linalg.norm(position - squad)), record, position))
    candidates.sort(key=lambda item: item[0])
    best = None
    for straight, record, position in candidates[:PLAN_CANDIDATES]:
        distance = walking_distance(chart.road, chart.regions, squad, position)
        if not np.isfinite(distance):
            rejected.append({'id': record['id'], 'reason': 'no_road_route'})
            continue
        if best is None or distance < best['distance']:
            best = {'kind': 'enemy', 'id': record['id'], 'position': position.tolist(),
                    'distance': round(distance, 1), 'straight': round(straight, 1),
                    'enemy_kind': record['kind']}
    for straight, record, _ in candidates[PLAN_CANDIDATES:]:
        rejected.append({'id': record['id'], 'reason': 'beyond_candidate_budget'})
    return best, rejected


def frontier_point(state, chart, snapshot, squad):
    """小队所在分量里最近的未观测落点；没有可去的前沿时返回 None。

    前沿按粗网格判断：该分量覆盖到、但小地图没覆盖过的格子即未观测。选中格后在格内取离格心
    最近的本分量道路像素作为落点——格心本身可能不在路上，直接拿去规划会被吸附到别处。
    """
    key = chart.surface_key(snapshot)
    try:
        mask = chart.component_mask(key, squad)
    except (ValueError, KeyError):
        # 小队位置缺少本层道路证据，这一拍不给前沿目标。
        return None
    coverage = cell_coverage(mask)
    unseen = (coverage > 0) & (state.observed[:coverage.shape[0], :coverage.shape[1]] == 0)
    rows, columns = np.nonzero(unseen)
    if not len(rows):
        return None
    centers = np.column_stack((columns, rows)).astype(float) * OBSERVED_CELL + OBSERVED_CELL / 2
    spans = np.linalg.norm(centers - np.asarray(squad, float), axis=1)
    order = np.argsort(spans)
    for index in order:
        if spans[index] > FRONTIER_LIMIT:
            break
        row, column = int(rows[index]), int(columns[index])
        top, left = row * OBSERVED_CELL, column * OBSERVED_CELL
        window = mask[top:top + OBSERVED_CELL, left:left + OBSERVED_CELL]
        ys, xs = np.nonzero(window)
        if not len(xs):
            continue
        points = np.column_stack((xs + left, ys + top)).astype(float)
        nearest = points[np.argmin(np.linalg.norm(points - centers[index], axis=1))]
        return {'kind': 'explore', 'id': f'cell-{column}-{row}', 'position': nearest.tolist(),
                'distance': round(float(spans[index]), 1), 'unseen_cells': int(unseen.sum())}
    return None


def choose_goal(state, chart, snapshot):
    """挑下一个目标，返回目标描述；没有可做的事情时 kind 为 none。

    要求本帧定位已被接受且静态层支持当前坐标模型，否则不给目标——坐标不可信时规划出来的
    落点也不可信。`blocking` 里的封锁本期不参与可达判定，只随结果一并报出来，供日志留证。
    """
    blocked = [record['id'] for record in state.blocking()]
    if snapshot.get('localization') != 'accepted' or snapshot.get('squad') is None:
        return {'kind': 'none', 'reason': 'localization_unavailable', 'blocking': blocked}
    if not getattr(chart, 'supported', False) or chart.road is None:
        return {'kind': 'none', 'reason': 'coordinate_model_unsupported', 'blocking': blocked}
    squad = np.asarray(snapshot['squad'], float)
    key = chart.surface_key(snapshot)
    goal, rejected = nearest_enemy(state, chart, squad, key)
    if goal is not None:
        goal['reason'] = 'nearest_pending_enemy'
        goal['rejected'] = rejected
        goal['blocking'] = blocked
        return goal
    goal = frontier_point(state, chart, snapshot, squad)
    if goal is not None:
        goal['reason'] = 'nearest_unobserved_cell'
        goal['rejected'] = rejected
        goal['blocking'] = blocked
        return goal
    return {'kind': 'none', 'reason': 'no_reachable_target', 'rejected': rejected, 'blocking': blocked}


def describe(goal):
    """把目标压成一行日志：类型、编号、落点、距离与选它的理由。"""
    if goal['kind'] == 'none':
        detail = f"无目标（{goal['reason']}）"
    else:
        position = [round(value, 1) for value in goal['position']]
        detail = f"{goal['kind']} {goal['id']} @ {position}，距离 {goal['distance']}（{goal['reason']}）"
    rejected = goal.get('rejected') or []
    if rejected:
        detail += '；否决 ' + '、'.join(f"{item['id']}:{item['reason']}" for item in rejected[:4])
    if goal.get('blocking'):
        detail += '；封锁 ' + '、'.join(goal['blocking'][:4])
    return detail
