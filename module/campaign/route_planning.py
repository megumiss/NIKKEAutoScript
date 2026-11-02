"""主线路程目标与执行目标：机关优先，主线其次，无目的地时沿用原策略。"""

import numpy as np

from module.campaign.run_state import COMBAT_ENEMY_KINDS

from module.campaign.navigation.ex_bypass import segment_clear
from module.campaign.navigation.pathfinding import NoConnectedRoad
from module.campaign.navigation.targets import ROAD_TOLERANCE, walking_distance
from module.campaign.subgoals import RoadView

# 路段推进距离使用章节底图像素；沿途敌人范围由当前道路截面决定，不设固定半径。
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


def same_screen_mechanism(snapshot, item):
    """判断指定机关是否在当前帧以 off 状态可见，供新路线选择与直点共用。

    有标注时按编号匹配；场景兜底的未标注机关按 25 底图像素内的位置匹配。
    该容差只用于身份关联，不是选择机关的距离门槛。无 kind 的地图标注按机关处理，
    其他目标类型不参与可见直点判断。
    """
    if item.get('kind', 'mechanism') != 'mechanism':
        return False
    for observed in snapshot.get('mechanisms') or []:
        if observed.get('state') != 'off':
            continue
        if observed.get('id') == item['id']:
            return True
        if (observed.get('id') is None
                and np.linalg.norm(np.asarray(observed['position']) - np.asarray(item['position'])) <= 25):
            return True
    return False


def refresh_route(state, chart, snapshot, cut):
    """保持未完成路程目标，只有完成、失败冷却或机关抢占主线时更换。

    已知开启和导航已到点的机关均排除；新机关路程只从当前帧可见的 off 检测发起，
    不设小队或敌人距离门槛，但必须在切开封锁后的道路上可达。已发起的路程跨屏继续。
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
        if record.get('state') == 'on' or state.cooling(item['id']) or not same_screen_mechanism(snapshot, item):
            continue
        route = route_path(cut, squad, item['position'])
        if route is None:
            continue
        distance = float(route[1][-1])
        if best is None or distance < best['distance']:
            best = dict(kind='mechanism', id=item['id'], position=item['position'], distance=round(distance, 1),
                        source=item.get('source', 'annotation'), reason='visible_reachable_mechanism')
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


def route_road_section(cut, point, path):
    """判断敌人所在连续道路截面是否与执行路径相交，返回截面端点及长度，不相交返回 None。

    point/path 均为章节底图像素。先在导航既有 ROAD_TOLERANCE 内将标记中心吸附到真实
    道路，再求该道路点在 A* 折线上的最近投影，不只比较离散路径节点。投影位于路段内部时
    截面垂直该段，落在路径端点时沿道路点到端点扫描，也覆盖小队身后仍处于同片路面的敌人。
    截面逐像素检查，遇到背景、墙、光幕或地图边缘立即截断，不跨越空隙连接另一条路。
    道路宽度自然决定候选范围，不设敌人距离或绕行门限；同一连通分量并不能替代此判据。
    吸附仅容忍标记的道路边缘误差，不膨胀道路；越界、非有限坐标和无可行截面都拒绝。
    """
    point, path = np.asarray(point, float), np.asarray(path, float)
    road = cut.road
    if (road is None or road.ndim != 2 or point.shape != (2,) or not np.isfinite(point).all()
            or path.ndim != 2 or path.shape[1] != 2 or not len(path) or not np.isfinite(path).all()):
        return None
    height, width = road.shape
    if np.any(point < 0) or np.any(point >= [width, height]):
        return None
    x, y = np.floor(point).astype(int)
    radius = int(np.ceil(ROAD_TOLERANCE))
    left, top = max(0, x - radius), max(0, y - radius)
    right, bottom = min(width, x + radius + 1), min(height, y + radius + 1)
    rows, columns = np.nonzero(road[top:bottom, left:right])
    if not len(rows):
        return None
    points = np.column_stack((columns + left, rows + top))
    spans = np.linalg.norm(points - point, axis=1)
    nearest = int(np.argmin(spans))
    if spans[nearest] > ROAD_TOLERANCE:
        return None
    road_point = points[nearest].astype(float)
    if len(path) == 1:
        route_point = path[0]
    else:
        segments = np.diff(path, axis=0)
        squared = np.sum(segments * segments, axis=1)
        ratios = np.divide(np.sum((road_point - path[:-1]) * segments, axis=1), squared,
                           out=np.zeros(len(segments)), where=squared > 0)
        projected = path[:-1] + np.clip(ratios, 0, 1)[:, None] * segments
        route_point = projected[int(np.argmin(np.linalg.norm(projected - road_point, axis=1)))]
    if not segment_clear(road, road_point, route_point):
        return None
    return dict(road_point=road_point.tolist(), route_point=route_point.tolist(),
                length=round(float(np.linalg.norm(route_point - road_point)), 1))


def route_enemy_candidates(cut, squad, target, path, distances, enemies):
    """按道路距离排列实际路径上的敌人记录，不赋予历史记录可战斗状态。

    仅接受敌人所在道路截面与 A* 路径相交、且到敌人与目的地都有可行道路的记录。
    道路边界决定截面范围，实际路程只排序，不使用固定走廊半径或额外绕行门限。
    执行目标和历史线索复核共用此几何条件；调用方负责类型、确认状态与冷却检查。
    """
    candidates = []
    home = cut.component(squad)
    for enemy in enemies:
        point = np.asarray(enemy['position'], float)
        if route_road_section(cut, point, path) is None:
            continue
        if home and cut.component(point) != home:
            continue
        first = walking_distance(cut.road, cut.regions, squad, point)
        last = walking_distance(cut.road, cut.regions, point, target)
        if not np.isfinite(first + last):
            continue
        candidates.append((first, enemy))
    return sorted(candidates, key=lambda pair: pair[0])


def route_enemy(state, cut, squad, target, path, distances):
    """选路程前方的第一个普通敌人或 Boss，忽略别条支路上的全局最近敌人。

    候选所在连续道路截面必须与实际路径相交；不因道路较宽或敌人在路径起点后方就按距离排除。
    截面遇到背景、墙或光幕立即停止，小队分量为零时仍以 A* 复核。冷却敌人返回明确暂停目标。
    仍有可处理普通敌人时 Boss 被跳过，主线路径不能绕过 boss_ready 门禁。
    """
    enemies = [enemy for enemy in state.confirmed_enemies()
               if enemy['kind'] in COMBAT_ENEMY_KINDS and enemy['status'] != 'cleared'
               and (enemy['kind'] != 'boss' or boss_ready(state))]
    candidates = route_enemy_candidates(cut, squad, target, path, distances, enemies)
    if not candidates:
        return None
    distance, enemy = min(candidates, key=lambda pair: pair[0])
    if enemy['status'] != 'pending' or state.cooling(enemy['id']):
        return dict(kind='none', reason='route_enemy_unavailable', for_target=enemy['id'])
    return dict(kind='enemy', id=enemy['id'], position=enemy['position'], enemy_kind=enemy['kind'],
                distance=round(distance, 1), reason='enemy_on_route',
                road_section=route_road_section(cut, enemy['position'], path))


def route_action(state, cut, squad, destination, visible=False):
    """优先完整执行当前路线上的敌人，可见机关直接执行，其余远目标按距离拆段。

    候选必须靠近通往目的地的实际 A* 路径且绕行有限；同一汇合区域的其他道路不能抢占执行目标。
    已选敌人不按距离拆段，交导航处理目标入屏与脚圈确认。visible 仅表示目的地是当前帧
    可见的 off 机关，此时不生成 advance，直接交执行层尝试检测框直点；不可见机关跨屏继续拆段。
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
    if distances[-1] > ADVANCE_DISTANCE and not visible:
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
