"""主线路程目标与短段执行目标：机关优先，主线其次，无目的地时沿用原策略。"""

from copy import copy

import cv2
import numpy as np

from module.campaign.navigation.pathfinding import NoConnectedRoad
from module.campaign.navigation.targets import walking_distance
from module.campaign.route_branches import branch_enemy
from module.campaign.subgoals import RoadView

# 以下距离均为章节底图像素；仅限制主动机关及路段长度，不放宽道路连通性。
MECHANISM_NEAR_SQUAD = 160.
MECHANISM_NEAR_ENEMY = 200.
ROUTE_CORRIDOR = 48.
ROUTE_DETOUR = 64.
ADVANCE_DISTANCE = 200.
# 短段末端不能落到敌人触发范围内，否则战斗会被记到 advance 而不是敌人编号。
ENEMY_APPROACH_MARGIN = 64.


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


def refresh_route(state, chart, snapshot, cut):
    """保持未完成路程目标，只有完成、失败冷却或机关抢占主线时更换。

    已知开启和导航已到点的机关均排除；未知机关仅在小队沿道路接近时补踩。
    场景发现的未开启机关还要求有邻近脚圈证据及道路邻近敌人，不能仅凭屏幕距离抢占。
    主线只使用已通过场景与小地图身份核验的稳定敌人编号，漏一帧标记不丢弃目的地。
    """
    current = state.route_goal
    if current is not None:
        done = (state.mechanisms.get(current['id'], {}).get('state') == 'on'
                if current['kind'] == 'mechanism'
                else state.enemies.get(current['id'], {}).get('status') != 'pending')
        if done:
            state.route_branches.pop((current['kind'], current['id']), None)
        if done or state.cooling(current['id']):
            current = None
        elif current['kind'] == 'story':
            current = dict(current, position=state.enemies[current['id']]['position'])
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
                for enemy in state.pending_enemies() if enemy['kind'] == 'normal'
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
        if enemy and enemy['status'] == 'pending' and not state.cooling(enemy['id']):
            current = dict(kind='story', id=enemy['id'], position=enemy['position'], reason='main_story_marker')
    state.route_goal = current
    return current


def route_enemy(state, cut, squad, target, path, distances):
    """选路程前方的第一个普通敌人，忽略别条支路上的全局最近敌人。

    候选须接近实际路径，且经该敌人的道路绕行不超过 ROUTE_DETOUR；距离与分量同时校验，
    防止把直线相邻但隔墙的敌人算在沿途。冷却中的沿途敌人不能静默越过，返回明确的暂停目标。
    """
    candidates = []
    full = float(distances[-1] + np.linalg.norm(path[-1] - np.asarray(target)))
    for enemy in state.enemies.values():
        if enemy['kind'] != 'normal' or enemy['status'] == 'cleared':
            continue
        point = np.asarray(enemy['position'], float)
        nearest = int(np.argmin(np.linalg.norm(path - point, axis=1)))
        if np.linalg.norm(path[nearest] - point) > ROUTE_CORRIDOR:
            continue
        if cut.component(point) != cut.component(squad):
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
    """把目的地拆成沿途／汇合岔路敌人或最长 ADVANCE_DISTANCE 的步行段。

    只有最后一段机关使用 mechanism 类型，短段到达不能提前把机关记为开启。
    主线目的地始终以敌人身份执行，战斗归属不因路程层变成 story 而丢失。
    汇合段两侧未清完不能离开，远处支路敌人的推进段沿该敌人的路径生成，不能继续向目的地走。
    不可达时交回光幕／电梯规划。
    """
    route = route_path(cut, squad, destination['position'])
    if route is None:
        return None
    branch = branch_enemy(state, cut, squad, destination)
    target = destination['position']
    if branch is not None:
        target = branch['position']
        route = route_path(cut, squad, target)
        if route is None:
            return None
    path, distances = route
    enemy = route_enemy(state, cut, squad, target, path, distances)
    if branch is not None:
        crosses_destination = (enemy is not None and enemy.get('id') == destination['id'])
        if destination['kind'] == 'mechanism':
            crosses_destination = np.linalg.norm(path - destination['position'], axis=1).min() <= 32
        if crosses_destination:
            return branch_detour(state, cut, squad, branch, destination)
    if enemy is not None and (enemy['kind'] == 'none'
                              or enemy['distance'] <= ADVANCE_DISTANCE + ENEMY_APPROACH_MARGIN):
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
    if goal['kind'] == 'story':
        goal.update(kind='enemy', enemy_kind='normal', reason='main_story_marker')
    return goal


def branch_detour(state, cut, squad, enemy, destination):
    """换边路径经过最终目标时先退回另一端，避免提前触发主线战斗或踩机关而丢失待清岔路。

    临时避开目的地周围 64 底图像素，只用于寻找短段落点，不修改光幕或实际导航的道路。
    每个落点还须在原切开图上复查路径，保证移动闭环重新寻路时也不会抄近路穿过目的地。
    无法取得至少 32 像素的安全推进段时暂停，不能把绕行失败当作岔路已清完。
    """
    avoid = copy(cut)
    avoid.road = cut.road.copy()
    cv2.circle(avoid.road, tuple(np.rint(destination['position']).astype(int)), int(ENEMY_APPROACH_MARGIN), 0, -1)
    avoid.labels = cv2.connectedComponents(avoid.road, connectivity=8)[1]
    avoid.regions = avoid.labels[::8, ::8]
    alternative = route_path(avoid, squad, enemy['position'])
    if alternative is not None:
        path, distances = alternative
        indices = np.flatnonzero((distances >= 32) & (distances <= ADVANCE_DISTANCE))
        for index in indices[::-1]:
            point = path[index]
            original = route_path(cut, squad, point)
            if original is None:
                continue
            actual_path, actual_distances = original
            if (actual_distances[-1] > ADVANCE_DISTANCE
                    or np.linalg.norm(actual_path - destination['position'], axis=1).min() <= ENEMY_APPROACH_MARGIN):
                continue
            front = route_enemy(state, cut, squad, point, actual_path, actual_distances)
            if front is not None:
                return front
            identifier = f"advance-{destination['id']}-{int(point[0])}-{int(point[1])}"
            if state.cooling(identifier):
                break
            return dict(kind='advance', id=identifier, position=point.tolist(),
                        distance=round(float(actual_distances[-1]), 1), reason='advance_on_route',
                        for_target=destination['id'])
    return dict(kind='none', reason='route_branch_unavailable', for_target=destination['id'])
