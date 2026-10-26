"""推图每一拍的目标选择：从运行状态与切开后的路网里挑出下一个该去的点。

决策层不碰设备也不点击：输入是 `RunState`、章节 `StaticMap`、按封锁切开的 `CutRoads` 与本帧快照，
更新运行状态中的路程目的地并返回执行目标，执行与否由调用方决定。同一套决策可离线回放校验。
可达性与行走距离一律走切开图，所以规划不会把小队送去撞光幕；原图只用来判断「这个目标是不是
只被光幕挡住」——是的话就该去复核封锁或踩机关，而不是当成走不通换目标。
优先处理机关关联的道路复核；剧情按机关、主线目的地逐段推进，无目的地才找全分量最近敌人。
机关解封先到点、再回封锁口复核；电梯按有向航段和失败冷却过滤。
"""

import numpy as np

from module.campaign.run_state import COMBAT_ENEMY_KINDS

from module.campaign.navigation.targets import walking_distance
from module.campaign.report import (BLOCK_STATUS, GOAL_KINDS, GOAL_REASONS, REJECT_REASONS, TARGET_SOURCES,
                                    label, points_text)
from module.campaign.run_state import OBSERVED_CELL, located
from module.campaign.subgoals import elevator_goal, mechanism_goal, review_goal
from module.campaign.route_planning import refresh_route, route_action

# 收集品沿用有限候选预算；敌人必须比较全部候选，否则会漏掉直线较远、路程却更近的前置敌人。
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

    小队分量可信时先按分量过滤；小队定位略偏出道路、分量为零时改用逐候选 A* 判断，
    不能把圆环覆盖范围内、实际可达的近距离敌人全部误删。直线距离只决定试算顺序。
    """
    squad = np.asarray(squad, float)
    home = cut.component(squad)
    rejected, candidates = [], []
    for record in state.pending_enemies():
        if record['kind'] not in COMBAT_ENEMY_KINDS:
            continue
        position = np.asarray(record['position'], float)
        if home and cut.component(position) != home:
            rejected.append({'id': record['id'], 'reason': 'different_component', 'position': position.tolist()})
            continue
        candidates.append((float(np.linalg.norm(position - squad)), record, position))
    candidates.sort(key=lambda item: item[0])
    best = None
    for straight, record, position in candidates:
        distance = walking_distance(cut.road, cut.regions, squad, position)
        if not np.isfinite(distance):
            reason = 'different_component' if not home and cut.component(position) else 'no_road_route'
            rejected.append({'id': record['id'], 'reason': reason, 'position': position.tolist()})
            continue
        if best is None or distance < best['distance']:
            best = {'kind': 'enemy', 'id': record['id'], 'position': position.tolist(),
                    'distance': round(distance, 1), 'straight': round(straight, 1),
                    'enemy_kind': record['kind']}
    return best, rejected


def enemy_hint_goal(state, cut, squad):
    """选择道路距离最近的已知敌人线索，作为侦察目标而不是直接战斗目标。

    战后当前小地图尚未重新确认其他敌人时，先沿原可靠坐标补视野，避免把无关空地
    设为黄色执行落点。返回 explore 类型，只有重新识别到敌人后才能由 nearest_enemy 战斗。
    """
    best = None
    for record in state.enemy_hints():
        if record['kind'] not in COMBAT_ENEMY_KINDS:
            continue
        position = np.asarray(record['position'], float)
        distance = walking_distance(cut.road, cut.regions, squad, position)
        if not np.isfinite(distance) or (best is not None and distance >= best['distance']):
            continue
        best = dict(kind='explore', id=f"hint-{record['id']}", position=position.tolist(),
                    distance=round(distance, 1), for_enemy=record['id'], reason='review_enemy_hint')
    return best


def blocked_candidate(state, chart, cut, squad, rejected):
    """被否决的候选里挑一个「只被光幕挡住」的，给出拦路封锁与（必要时）复核站位。

    只看 different_component 的否决项：它们在切开图上不可达，但如果在原图可达，
    说明隔开它们的是光幕而不是地形。拦路封锁用逐个撤销试出来。
    站位只给 stale 封锁：那种是「记过但之后没再看过」，得走到看得见的地方确认它还在不在。
    active 是最近一次在视野内时还在，再去看一眼没有新信息，而且站位就在封锁旁边，
    会变成反复往同一处走的自锁；active 由机关解封子目标处理。
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
        identifier = f'cell-{column}-{row}'
        if state.cooling(identifier):
            continue
        distance = walking_distance(cut.road, cut.regions, squad, nearest)
        if not np.isfinite(distance):
            continue
        return {'kind': 'explore', 'id': identifier, 'position': nearest.tolist(),
                'distance': round(distance, 1), 'unseen_cells': int(unseen.sum())}
    return None


def choose_goal(state, chart, snapshot, cut, purpose='story'):
    """挑下一个目标，返回目标描述；没有可做的事情时 kind 为 none。

    要求本帧定位可用（配准被接受，或战后沿用战斗前的坐标）且静态层支持当前坐标模型，否则不给目标——
    坐标不可信时规划出来的落点也不可信。cut 必须已按 `state.blocking()` 刷新过，否则可达性判的是过期的封锁集合。
    """
    blocked = [record['id'] for record in state.blocking()]
    if not located(snapshot) or snapshot.get('squad') is None:
        return {'kind': 'none', 'reason': 'localization_unavailable', 'blocking': blocked}
    if not getattr(chart, 'supported', False) or chart.road is None or cut.road is None:
        return {'kind': 'none', 'reason': 'coordinate_model_unsupported', 'blocking': blocked}
    squad = np.asarray(snapshot['squad'], float)
    if squad.shape != (2,) or not np.isfinite(squad).all():
        return {'kind': 'none', 'reason': 'localization_unavailable', 'blocking': blocked}
    goal, rejected = None, []
    for identifier in list(state.pending_reviews):
        record = state.blocks.get(identifier)
        if record is None or record['status'] == 'cleared':
            continue
        goal = review_goal(state, cut, squad, identifier)
        if goal is not None:
            break
        return {'kind': 'none', 'reason': 'curtain_review_unavailable', 'blocking': blocked}
    if purpose == 'collectible':
        candidates = [item for item in chart.points if item['category'] == f'{state.difficulty}_collectible'
                      and not state.cooling(item['id'])]
        # 先按分量过滤再截取行走距离预算：先截再滤时，直线最近的几个若都在
        # 光幕或墙的另一侧，同侧可达的收集品就永远轮不到。
        home = cut.component(squad)
        reachable = [item for item in candidates if home and cut.component(item['position']) == home]
        if goal is None:
            nearest = sorted(reachable, key=lambda item: np.linalg.norm(np.asarray(item['position']) - squad))
            distances = [(walking_distance(cut.road, cut.regions, squad, item['position']), item)
                         for item in nearest[:PLAN_CANDIDATES]]
            distances = [item for item in distances if np.isfinite(item[0])]
            if distances:
                distance, item = min(distances, key=lambda pair: pair[0])
                goal = dict(kind='collectible', id=item['id'], position=item['position'],
                            distance=round(distance, 1), reason='nearest_annotated_collectible')
        rejected = [dict(id=item['id'], position=item['position'], reason='different_component')
                    for item in candidates if item not in reachable]
    else:
        candidates = [item for item in state.pending_enemies() if item['kind'] in COMBAT_ENEMY_KINDS]
        if goal is None:
            destination = refresh_route(state, chart, snapshot, cut)
            if destination is None:
                goal, rejected = nearest_enemy(state, cut, squad)
            else:
                goal = route_action(state, cut, squad, destination)
                candidates = [destination]
                if goal is None:
                    rejected = [dict(id=destination['id'], position=destination['position'],
                                     reason='different_component')]
    if goal is None:
        detour = blocked_candidate(state, chart, cut, squad, rejected)
        if detour is not None:
            record = state.blocks[detour['block']]
            if record['status'] == 'stale' or record['id'] in state.pending_reviews:
                goal = review_goal(state, cut, squad, record['id'], detour['target'])
            else:
                goal = mechanism_goal(state, chart, cut, squad, record, detour['target'])
                if goal is not None and purpose == 'story':
                    state.route_goal = dict(goal)
                    goal = route_action(state, cut, squad, goal)
            if goal is None:
                goal = {'kind': 'none', 'reason': 'blocked_by_curtain', 'blocked_by': detour['culprits'],
                        'for_target': detour['target']}
    if goal is None and purpose == 'story' and state.route_goal is not None:
        goal, rejected = nearest_enemy(state, cut, squad)
    if goal is None:
        goal = elevator_goal(state, chart, cut, squad, candidates)
    if goal is None and purpose == 'story' and state.route_goal is not None:
        goal = {'kind': 'none', 'reason': 'route_unreachable', 'for_target': state.route_goal['id']}
    if goal is None and purpose == 'story':
        goal = enemy_hint_goal(state, cut, squad)
    if goal is None:
        goal = frontier_point(state, cut, squad)
    if goal is None:
        goal = {'kind': 'none', 'reason': 'no_reachable_target'}
    goal.setdefault('reason', {'enemy': 'nearest_pending_enemy', 'explore': 'nearest_unobserved_cell',
                               'review_block': 'review_blocking_curtain'}.get(goal['kind'], 'unknown'))
    goal['rejected'] = rejected
    goal['blocking'] = blocked
    return goal


def _describe_action(goal):
    """按目标类型拼「做什么、去哪儿、多远、依据」；各类型可用字段不同，格式各自固定。"""
    kind = goal['kind']
    position = points_text(goal['position'])
    if kind == 'review_block':
        detail = (f"复核光幕 {goal['id']}（记录状态：{label(BLOCK_STATUS, goal.get('block_status'))}），"
                  f"复核位置 {position}，光幕位置 {points_text(goal['block_point'])}")
        if goal.get('for_target'):
            detail += f"，后续目标 {goal['for_target']}"
    elif kind == 'mechanism':
        detail = f"触发地面机关 {goal['id']}，位置 {position}"
        if goal.get('block'):
            detail += f"，关联光幕 {goal['block']}"
        detail += f"（坐标来自{label(TARGET_SOURCES, goal.get('source'))}）"
    elif kind == 'elevator':
        detail = f"电梯 {goal['id']}：入口 {position}，出口 {points_text(goal['leg']['exit'])}"
        if goal.get('for_target'):
            detail += f"，后续目标 {goal['for_target']}"
    elif kind == 'explore':
        detail = f"{label(GOAL_KINDS, kind)} {goal['id']}，位置 {position}"
        if goal.get('unseen_cells') is not None:
            detail += f"，未探索区域 {goal['unseen_cells']} 格"
    else:
        name = 'Boss' if goal.get('enemy_kind') == 'boss' else label(GOAL_KINDS, kind)
        detail = f"{name} {goal['id']}，位置 {position}"
    if goal.get('distance') is not None:
        detail += f"，道路距离 {goal['distance']} 像素"
        if goal.get('straight') is not None:
            detail += f"（直线 {goal['straight']}）"
    detail += f"；选择依据：{label(GOAL_REASONS, goal.get('reason'))}"
    return detail


def _describe_nothing(goal):
    """没有可执行目标时说明卡在哪一步；被光幕挡住时补上是谁挡的。"""
    detail = f"无可执行目标（原因：{label(GOAL_REASONS, goal.get('reason'))}）"
    if goal.get('for_target'):
        detail += f"，关联目标 {goal['for_target']}"
    if goal.get('blocked_by'):
        detail += '，阻断光幕 ' + '、'.join(goal['blocked_by'][:4])
    return detail


def describe(goal):
    """把目标压成一行中文日志：要做什么、去哪儿、多远、为什么是它，以及本轮排除了谁。

    内部编号（enemy-3、block-1、cell-15-44 之类）保留原样：中文说明让人看懂，编号让人能在日志里
    检索到同一个目标。查不到的标识由 report.label 原样输出，新增字段不会在日志里消失。
    """
    detail = _describe_nothing(goal) if goal['kind'] == 'none' else _describe_action(goal)
    rejected = goal.get('rejected') or []
    if rejected:
        detail += '；本轮排除 ' + '、'.join(
            f"{item['id']}（{label(REJECT_REASONS, item['reason'])}）" for item in rejected[:4])
    if goal.get('blocking'):
        detail += '；当前阻断光幕 ' + '、'.join(goal['blocking'][:4])
    return detail
