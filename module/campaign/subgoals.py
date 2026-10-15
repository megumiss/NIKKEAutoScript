"""推图子目标规划：机关解封与有向电梯；纯计算，不产生输入。"""

import numpy as np

from module.campaign.navigation import walking_distance
from module.campaign_prototype.elevator_navigation import plan_elevators
from module.campaign_prototype.probe import Localizer, NoConnectedRoad


class RoadView:
    """把切开图包成共享电梯规划器需要的道路接口。"""

    def __init__(self, cut):
        """只持有当前切开图，不另加载地图资源或复制道路。"""
        self.cut = cut
        self.road, self.route_labels = cut.road, cut.regions

    def route(self, start, target):
        """先查原坐标分量，再委托共享 A*，防止吸附跨过切口。"""
        home = self.cut.component(start)
        if not home or self.cut.component(target) != home:
            raise NoConnectedRoad('电梯步行段跨越不可通行分区。')
        return Localizer.route(self, start, target)


def available_annotations(state, chart):
    """过滤失败方向并转换为共享电梯规划器的有向点标注；不修改原始地图包。"""
    objects, connections = [], []
    for link in chart.transfers:
        directions = [(link['entry'], link['exit'])]
        if not link['directed']:
            directions.append((link['exit'], link['entry']))
        for index, (entry, exit_point) in enumerate(directions):
            leg = dict(connection=link['id'], entry=entry, exit=exit_point)
            if not state.transfer_available(leg):
                continue
            source, destination = f"{link['id']}-{index}-in", f"{link['id']}-{index}-out"
            objects.extend([dict(id=source, category='ground_elevator', type='point', points=[entry]),
                            dict(id=destination, category='ground_elevator', type='point', points=[exit_point])])
            connections.append(dict(id=link['id'], category='elevator_connection', directed=True,
                                    **{'from': source, 'to': destination}))
    return {'objects': objects, 'connections': connections}


def elevator_goal(state, chart, cut, squad, targets):
    """跨分量目标有可用电梯时返回第一段；走完出口后按新快照重选最终目标。"""
    if not chart.transfers:
        return None
    annotations = available_annotations(state, chart)
    if not annotations['connections']:
        return None
    localizer = RoadView(cut)
    ordered = sorted(targets, key=lambda item: np.linalg.norm(np.asarray(item['position']) - squad))
    for target in ordered[:4]:
        try:
            legs = plan_elevators(localizer, annotations, squad, np.asarray(target['position']))
        except NoConnectedRoad:
            continue
        if not legs:
            continue
        leg = legs[0]
        return dict(kind='elevator', id=leg['connection'], position=leg['entry'], leg=leg,
                    distance=round(walking_distance(cut.road, cut.regions, squad, leg['entry']), 1),
                    for_target=target['id'], reason='transfer_to_pending_target')
    return None


def mechanism_goal(state, chart, cut, squad, block, target):
    """选同侧尚未为该封锁试过的机关；有点标注时不用场景检测替代标注坐标。"""
    annotated = [item for item in chart.points if item['category'] == 'ground_mechanism']
    candidates = annotated or [item for item in state.mechanisms.values() if item.get('source') == 'scene_fallback']
    home = cut.component(squad)
    if not home:
        return None
    candidates = [item for item in candidates if not state.tried_for(item['id'], block['id'])
                  and not state.cooling(item['id']) and cut.component(item['position']) == home]
    candidates.sort(key=lambda item: np.linalg.norm(np.asarray(item['position']) - squad))
    best = None
    for item in candidates[:8]:
        distance = walking_distance(cut.road, cut.regions, squad, item['position'])
        if not np.isfinite(distance) or (best is not None and distance >= best['distance']):
            continue
        best = dict(kind='mechanism', id=item['id'], position=item['position'], distance=round(distance, 1),
                    block=block['id'], for_target=target, source=item.get('source', 'annotation'),
                    reason='try_mechanism_for_curtain')
    return best


def review_goal(state, cut, squad, identifier, target=None):
    """为待验证封锁给出同侧安全站位；冷却中的复核不再次派出。"""
    record = state.blocks.get(identifier)
    if record is None or record['status'] == 'cleared' or state.cooling(f'review-{identifier}'):
        return None
    point = cut.standoff(record, squad)
    if point is None:
        return None
    return dict(kind='review_block', id=identifier, position=point, block_point=record['point'],
                block_status=record['status'], for_target=target,
                distance=round(walking_distance(cut.road, cut.regions, squad, point), 1),
                reason='verify_mechanism_curtain' if identifier in state.pending_reviews else 'review_blocking_curtain')
