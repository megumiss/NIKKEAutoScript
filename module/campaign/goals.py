"""推图每一拍的目标选择：从运行状态与切开后的路网里挑出下一个该去的点。

纯函数层，不碰设备也不点击：输入是 `RunState`、章节 `StaticMap`、按封锁切开的 `CutRoads` 与本帧快照，
输出一个目标描述，执行与否由调用方决定。这样同一套决策可以拿现场帧离线回放校验。
可达性与行走距离一律走切开图，所以规划不会把小队送去撞光幕；原图只用来判断「这个目标是不是
只被光幕挡住」——是的话就该去复核封锁或踩机关，而不是当成走不通换目标。
优先级：最近的 pending 敌人 → 复核挡路的封锁 → 前沿探索 → 无进展。
踩机关解封与电梯航段还没接，`blocked_by_curtain` 会把挡路的封锁编号报出来等那一步接上。
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


def nearest_enemy(state, cut, squad):
    """切开图上行走距离最近的 pending 敌人；返回 (目标, 否决记录)。

    先按切开图的分量筛候选：跨分量的要么真走不通，要么被光幕隔开，后者由调用方按原图再判一次。
    分量判定在原始坐标上做，网格吸附会把相邻分量并到一起虚报可达。
    """
    squad = np.asarray(squad, float)
    home = cut.component(squad)
    rejected, candidates = [], []
    for record in state.pending_enemies():
        position = np.asarray(record['position'], float)
        if not home or cut.component(position) != home:
            rejected.append({'id': record['id'], 'reason': 'different_component', 'position': position.tolist()})
            continue
        candidates.append((float(np.linalg.norm(position - squad)), record, position))
    candidates.sort(key=lambda item: item[0])
    best = None
    for straight, record, position in candidates[:PLAN_CANDIDATES]:
        distance = walking_distance(cut.road, cut.regions, squad, position)
        if not np.isfinite(distance):
            rejected.append({'id': record['id'], 'reason': 'no_road_route', 'position': position.tolist()})
            continue
        if best is None or distance < best['distance']:
            best = {'kind': 'enemy', 'id': record['id'], 'position': position.tolist(),
                    'distance': round(distance, 1), 'straight': round(straight, 1),
                    'enemy_kind': record['kind']}
    for straight, record, position in candidates[PLAN_CANDIDATES:]:
        rejected.append({'id': record['id'], 'reason': 'beyond_candidate_budget', 'position': position.tolist()})
    return best, rejected


def blocked_candidate(state, chart, cut, squad, rejected):
    """被否决的候选里挑一个「只被光幕挡住」的，给出拦路封锁与（必要时）复核站位。

    只看 different_component 的否决项：它们在切开图上不可达，但如果在原图可达，
    说明隔开它们的是光幕而不是地形。拦路封锁用逐个撤销试出来。
    站位只给 stale 封锁：那种是「记过但之后没再看过」，得走到看得见的地方确认它还在不在。
    active 是最近一次在视野内时还在，再去看一眼没有新信息，而且站位就在封锁旁边，
    会变成反复往同一处走的自锁；该走机关解封，那条路留给后续接入。
    返回 None 表示没有这种候选。
    """
    squad = np.asarray(squad, float)
    for item in rejected:
        if item['reason'] != 'different_component':
            continue
        target = np.asarray(item['position'], float)
        if not cut.blocked(squad, target):
            continue
        culprits = cut.culprits(squad, target)
        if not culprits:
            continue
        identifier = culprits[0]
        record = state.blocks.get(identifier)
        if record is None:
            continue
        standoff = cut.standoff(record, squad) if record['status'] == 'stale' else None
        return {'block': identifier, 'status': record['status'], 'point': record['point'],
                'standoff': standoff, 'target': item['id'], 'culprits': culprits}
    return None


def frontier_point(state, cut, squad):
    """小队所在分量里最近的未观测落点；没有可去的前沿时返回 None。

    分量取切开图的：光幕另一侧的未观测区现在过不去，先不当前沿，等解封后自然会进候选。
    前沿按粗网格判断：该分量覆盖到、但小地图没覆盖过的格子即未观测。选中格后在格内取离格心
    最近的本分量道路像素作为落点——格心本身可能不在路上，直接拿去规划会被吸附到别处。
    """
    try:
        mask = cut.component_mask(squad)
    except (ValueError, KeyError):
        return None
    coverage = cell_coverage(mask)
    unseen = (coverage > 0) & (state.observed[:coverage.shape[0], :coverage.shape[1]] == 0)
    rows, columns = np.nonzero(unseen)
    if not len(rows):
        return None
    centers = np.column_stack((columns, rows)).astype(float) * OBSERVED_CELL + OBSERVED_CELL / 2
    spans = np.linalg.norm(centers - np.asarray(squad, float), axis=1)
    for index in np.argsort(spans):
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


def choose_goal(state, chart, snapshot, cut):
    """挑下一个目标，返回目标描述；没有可做的事情时 kind 为 none。

    要求本帧定位已被接受且静态层支持当前坐标模型，否则不给目标——坐标不可信时规划出来的
    落点也不可信。cut 必须已按 `state.blocking()` 刷新过，否则可达性判的是过期的封锁集合。
    """
    blocked = [record['id'] for record in state.blocking()]
    if snapshot.get('localization') != 'accepted' or snapshot.get('squad') is None:
        return {'kind': 'none', 'reason': 'localization_unavailable', 'blocking': blocked}
    if not getattr(chart, 'supported', False) or chart.road is None or cut.road is None:
        return {'kind': 'none', 'reason': 'coordinate_model_unsupported', 'blocking': blocked}
    squad = np.asarray(snapshot['squad'], float)
    goal, rejected = nearest_enemy(state, cut, squad)
    if goal is None:
        detour = blocked_candidate(state, chart, cut, squad, rejected)
        if detour is not None and detour['standoff'] is not None:
            goal = {'kind': 'review_block', 'id': detour['block'], 'position': detour['standoff'],
                    'distance': round(float(np.linalg.norm(np.asarray(detour['standoff'], float) - squad)), 1),
                    'block_point': detour['point'], 'block_status': detour['status'],
                    'for_target': detour['target']}
        elif detour is not None:
            # 找到了拦路封锁但同侧没有可站的落点：不猜落点，交给上层报无进展。
            goal = {'kind': 'none', 'reason': 'blocked_by_curtain', 'blocked_by': detour['culprits'],
                    'for_target': detour['target']}
    if goal is None:
        goal = frontier_point(state, cut, squad)
    if goal is None:
        goal = {'kind': 'none', 'reason': 'no_reachable_target'}
    goal.setdefault('reason', {'enemy': 'nearest_pending_enemy', 'explore': 'nearest_unobserved_cell',
                               'review_block': 'review_blocking_curtain'}.get(goal['kind'], 'unknown'))
    goal['rejected'] = rejected
    goal['blocking'] = blocked
    return goal


def describe(goal):
    """把目标压成一行日志：类型、编号、落点、距离与选它的理由。"""
    if goal['kind'] == 'none':
        detail = f"无目标（{goal['reason']}）"
        if goal.get('blocked_by'):
            detail += '，被封锁 ' + '、'.join(goal['blocked_by'])
    else:
        position = [round(value, 1) for value in goal['position']]
        detail = f"{goal['kind']} {goal['id']} @ {position}，距离 {goal['distance']}（{goal['reason']}）"
        if goal['kind'] == 'review_block':
            detail += f"，封锁点 {goal['block_point']}（{goal['block_status']}），为 {goal['for_target']}"
    rejected = goal.get('rejected') or []
    if rejected:
        detail += '；否决 ' + '、'.join(f"{item['id']}:{item['reason']}" for item in rejected[:4])
    if goal.get('blocking'):
        detail += '；封锁 ' + '、'.join(goal['blocking'][:4])
    return detail
