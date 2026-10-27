"""主线路程目标与执行目标：机关优先，主线其次，无目的地时沿用原策略。"""

import numpy as np

from module.campaign.run_state import COMBAT_ENEMY_KINDS

from module.campaign.navigation.pathfinding import NoConnectedRoad
from module.campaign.navigation.targets import walking_distance
from module.campaign.subgoals import RoadView

# 以下距离均为章节底图像素；仅限制主动机关及路段长度，不放宽道路连通性。
MECHANISM_NEAR_SQUAD = 160.
MECHANISM_NEAR_ENEMY = 200.
ROUTE_CORRIDOR = 48.
ROUTE_DETOUR = 64.
ADVANCE_DISTANCE = 200.


def route_path(cut, squad, target):
    """返回经分量与端点吸附检查的 A* 路径及累计路程；不可达时返回 None。

    路径只来自当前切开图。原图可达不能代替光幕阻断后的可达性，也不能用视野中心代替 squad。
    """
    try:
        _, path = RoadView(cut).route(squad, target)
    except (NoConnectedRoad, ValueError):
        return None
    if np.linalg.norm(path[-1] - np.asarray(target)) > 32:
        return None
    distances = np.r_[0., np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
    distances += np.linalg.norm(path[0] - np.asarray(squad))
    return path, distances


def boss_ready(state):
    """当前是否允许执行 Boss：没有可处理的普通敌人才放行。

    只依据小地图敌人类型和运行状态，不要求普通模式的主线菱形，因此困难模式同样适用。
    EX 不参与限制；冷却中的普通敌人暂不阻塞 Boss，避免失败冷却后无目标可执行。
    """
    return not any(enemy['kind'] == 'normal' for enemy in state.pending_enemies())


def refresh_route(state, chart, snapshot, cut):
    """保持未完成路程目标，只有完成、失败冷却或机关抢占主线时更换。

    已知开启和导航已到点的机关均排除；未知机关仅在小队沿道路接近时补踩。
    场景发现的未开启机关还要求有邻近脚圈证据及道路邻近敌人，不能仅凭屏幕距离抢占。
    主线可使用已核验的稳定敌人编号，或菱形下方唯一地面环的投影位置；漏一帧标记不丢弃目的地。
    """
    current = state.route_goal
    if current is not None:
        story_point = current['kind'] == 'story' and current['id'] not in state.enemies
        done = (state.mechanisms.get(current['id'], {}).get('state') == 'on'
                if current['kind'] == 'mechanism'
                else False if story_point else state.enemies.get(current['id'], {}).get('status') != 'pending')
        if done or state.cooling(current['id']):
            current = None
        elif current['kind'] == 'story' and not story_point:
            enemy = state.enemies[current['id']]
            current = (dict(current, position=enemy['position'], enemy_kind=enemy['kind'])
                       if enemy['kind'] in COMBAT_ENEMY_KINDS else None)
    if current is not None and current['kind'] == 'mechanism':
        state.route_goal = current
        return current
    squad = snapshot['squad']
    annotated = [item for item in chart.points if item['category'] == 'ground_mechanism']
    candidates = annotated or [item for item in state.mechanisms.values() if item.get('position') is not None]
    best = None
    for item in candidates:
        record = state.mechanisms.get(item['id'], {})
        if record.get('state') == 'on' or state.cooling(item['id']):
            continue
        route = route_path(cut, squad, item['position'])
        if route is None:
            continue
        distance = float(route[1][-1])
        reason = None
        if record.get('state') is None and distance <= MECHANISM_NEAR_SQUAD:
            reason = 'nearby_unseen_mechanism'
        elif record.get('state') == 'off' and (record.get('near_enemy') or distance <= MECHANISM_NEAR_SQUAD):
            near = distance <= MECHANISM_NEAR_SQUAD or any(
                walking_distance(cut.road, cut.regions, item['position'], enemy['position']) <= MECHANISM_NEAR_ENEMY
                for enemy in state.pending_enemies() if enemy['kind'] in COMBAT_ENEMY_KINDS
                and np.linalg.norm(np.asarray(enemy['position']) - item['position']) <= MECHANISM_NEAR_ENEMY)
            if near:
                reason = 'nearby_closed_mechanism'
        if reason is not None and (best is None or distance < best['distance']):
            best = dict(kind='mechanism', id=item['id'], position=item['position'], distance=round(distance, 1),
                        source=item.get('source', 'annotation'), reason=reason)
    if best is not None:
        current = best
    if current is None and state.story_target:
        enemy = state.enemies.get(state.story_target)
        if (enemy and enemy['kind'] in COMBAT_ENEMY_KINDS and enemy['status'] == 'pending'
                and not state.cooling(enemy['id'])):
            current = dict(kind='story', id=enemy['id'], position=enemy['position'],
                           enemy_kind=enemy['kind'], reason='main_story_marker')
    state.route_goal = current
    return current


def route_enemy(state, cut, squad, target, path, distances):
    """选路程前方的第一个普通敌人或 Boss，忽略别条支路上的全局最近敌人。

    候选须接近实际路径，且经该敌人的道路绕行不超过 ROUTE_DETOUR；小队分量为零时以 A* 复核，
    防止把直线相邻但隔墙的敌人算在沿途，也避免误删圆环覆盖的近敌。冷却敌人返回明确暂停目标。
    仍有可处理普通敌人时 Boss 被跳过，主线路径不能绕过 boss_ready 门禁。
    """
    candidates = []
    home = cut.component(squad)
    full = float(distances[-1] + np.linalg.norm(path[-1] - np.asarray(target)))
    for enemy in state.confirmed_enemies():
        if enemy['kind'] not in COMBAT_ENEMY_KINDS or enemy['status'] == 'cleared':
            continue
        if enemy['kind'] == 'boss' and not boss_ready(state):
            continue
        point = np.asarray(enemy['position'], float)
        nearest = int(np.argmin(np.linalg.norm(path - point, axis=1)))
        if np.linalg.norm(path[nearest] - point) > ROUTE_CORRIDOR:
            continue
        if home and cut.component(point) != home:
            continue
        first = walking_distance(cut.road, cut.regions, squad, point)
        last = walking_distance(cut.road, cut.regions, point, target)
        if not np.isfinite(first + last) or first + last - full > ROUTE_DETOUR:
            continue
        candidates.append((first, enemy))
    if not candidates:
        return None
    distance, enemy = min(candidates, key=lambda pair: pair[0])
    if enemy['status'] != 'pending' or state.cooling(enemy['id']):
        return dict(kind='none', reason='route_enemy_unavailable', for_target=enemy['id'])
    return dict(kind='enemy', id=enemy['id'], position=enemy['position'], enemy_kind=enemy['kind'],
                distance=round(distance, 1), reason='enemy_on_route')


def route_action(state, cut, squad, destination):
    """优先完整执行当前路线上的敌人，无敌人时才生成最长 ADVANCE_DISTANCE 的步行段。

    候选必须靠近通往目的地的实际 A* 路径且绕行有限；同一汇合区域的其他道路不能抢占执行目标。
    已选敌人不按距离拆段，交导航处理目标入屏与脚圈确认。
    只有最后一段机关使用 mechanism 类型，短段到达不能提前把机关记为开启。
    已绑定敌人的主线目的地以敌人身份执行，战斗归属不因路程层变成 story 而丢失；
    无敌人目的地保留 story 类型，到点后清除该段路程目标。不可达时交回光幕／电梯规划。
    """
    route = route_path(cut, squad, destination['position'])
    if route is None:
        return None
    path, distances = route
    enemy = route_enemy(state, cut, squad, destination['position'], path, distances)
    if enemy is not None:
        return enemy
    if distances[-1] > ADVANCE_DISTANCE:
        index = max(1, int(np.searchsorted(distances, ADVANCE_DISTANCE, side='right')) - 1)
        point = path[min(index, len(path) - 1)]
        identifier = f"advance-{destination['id']}-{int(point[0])}-{int(point[1])}"
        if state.cooling(identifier):
            return dict(kind='none', reason='route_step_unavailable', for_target=destination['id'])
        return dict(kind='advance', id=identifier, position=point.tolist(), distance=round(float(distances[index]), 1),
                    reason='advance_on_route', for_target=destination['id'])
    goal = dict(destination, distance=round(float(distances[-1]), 1))
    if goal['kind'] == 'story' and destination.get('enemy_kind') in COMBAT_ENEMY_KINDS:
        goal.update(kind='enemy', reason='main_story_marker')
    return goal


def planning_routes(cut, snapshot, goal, destination=None, state=None):
    """提供当前决策的实际道路折线，区分本段执行路线和最终目的地路线。

    仅接受可信小队起点，复用规划器同一切开图和端点吸附检查；不可达时留空，绝不画直线穿过封锁。
    电梯本段从当前位置画到入口，目的地路线从出口起算，传送连线由设施层展示。
    小目标不是电梯时，state 用于只读查找目的地需要的电梯，保证最终路线不随小目标切换消失。
    没有可执行小目标时仍保留可达的目的地路线。每次决策重建，避免换目标或换章后显示旧路径。
    """
    from module.campaign.run_state import located
    from module.campaign.subgoals import elevator_goal

    routes = {}
    if not located(snapshot) or snapshot.get('squad') is None:
        return routes
    transfer = goal if goal and goal.get('kind') == 'elevator' else None
    if transfer is None and destination is not None and state is not None:
        transfer = elevator_goal(state, cut.chart, cut, snapshot['squad'], [destination])
    for kind, target in (('execution', goal), ('destination', destination)):
        if not target or target.get('position') is None or target.get('kind') == 'none':
            continue
        start = transfer['leg']['exit'] if kind == 'destination' and transfer is not None else snapshot['squad']
        route = route_path(cut, start, target['position'])
        if route is None:
            continue
        path, distances = route
        routes[kind] = dict(target_id=target['id'], points=path.round(1).tolist(),
                            distance=round(float(distances[-1]), 1))
    return routes
