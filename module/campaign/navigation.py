"""章节地图上的下一个目标：最近的普通敌人，没有敌人时是附近的地面机关或电梯。"""

import types

import cv2
import numpy as np

from module.campaign_prototype.probe import Localizer as FlatLocalizer, NoConnectedRoad
from module.logger import logger

# 没有敌人时机关和电梯的搜索范围，量级按展开小地图一次能看到的道路取。
NEARBY_RADIUS = 480.0
# 目标点允许偏离道路的上限；A* 会把场外点吸附到最近网格，必须先挡住跨墙、跨层的候选。
# 依据大地图圆环的短轴覆盖半径（goto.SQUAD_REACH，地图像素）：小队站在路上时圆环能触到路外
# 圆环半径内的机关落点。取 20 而不是满额 28，是为留出跨墙误吸附的余量，先保守观察误选情况。
ROAD_TOLERANCE = 20.0
# A* 会把端点吸附到最近的净空网格；吸附上限按圆环覆盖半径取（介于短轴 28 与长轴 40 之间），
# 吸附超过它说明目标不在可通行道路网格上。
SNAP_RADIUS = 32.0

TARGET_CATEGORIES = ('ground_mechanism', 'ground_elevator')


def walking_distance(road, route_labels, start, target):
    """沿连通道路的行走距离；不连通或不可达时返回无穷大。

    road 为二值道路掩码，route_labels 为连通分区网格，无分区时为 None。
    路径来自平面原型的 A*，距离按路径长度计，比直线距离更能反映实际绕行；
    目标不在可通行网格上（或只有网格外的细路）时不报吸附点距离，按不可达处理。
    """
    shim = types.SimpleNamespace(road=road, route_labels=route_labels)
    try:
        _, path = FlatLocalizer.route(shim, np.asarray(start, float), np.asarray(target, float))
    except NoConnectedRoad:
        return float('inf')
    if np.linalg.norm(path[-1] - np.asarray(target, float)) > SNAP_RADIUS:
        return float('inf')
    return float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())


class TargetFinder:
    """选择下一个目标：可连通道路上最近的普通敌人，没有敌人时是附近的地面机关或电梯。

    平面地图使用整幅道路和连通分区；分层地图只用小队所在表面的连通道路，跨分区、跨表面的
    目标不会被选中。只有点状标注给出唯一目标点，折线和多边形机关、电梯不参与选择。
    道路、分区、标注与同层掩码都来自章节的 StaticMap，这里不自行加载。
    """

    def __init__(self, chart):
        """从章节 StaticMap 取出目标选择需要的道路、分区与标注。

        平面地图在这里就固定好整幅道路和连通分区；分层地图的道路要按小队所在表面逐帧取，
        所以 road 置空、分区不预存。遇到暂不支持的坐标模型只告警并把 supported 置假，
        find 会直接返回 None，让推图退回人工而不是拿错误的道路规划。
        """
        self.chart = chart
        self.chapter = chart.chapter
        self.model = chart.model
        self.supported = chart.supported
        if not self.supported:
            logger.warning(f'第 {self.chapter} 章地图模型 {self.model} 暂不支持目标规划，将不选择目标。')
        self.annotations = chart.annotations
        self.road = chart.road if self.model is None else None
        self.route_labels = chart.regions
        self._surface_warned = False

    def _walkable(self, snapshot, squad):
        """本次决策使用的道路掩码和分区标签；分层地图要求观测带表面信息。"""
        if self.model is None:
            return self.road, self.route_labels
        key = self.chart.surface_key(snapshot)
        return self.chart.component_mask(key, squad).astype(np.float32), None

    def _nearest(self, field, road, route_labels, squad, candidates, limit):
        """取行走距离最近且可达的候选；limit 对直线距离和行走距离同时生效。

        候选点必须先落在道路上，否则吸附到最近网格会跨过墙或换层，得出虚假的可达距离。
        """
        best = None
        height, width = field.shape
        source = 0
        if route_labels is not None:
            sx, sy = int(squad[0]) // 8, int(squad[1]) // 8
            if 0 <= sx < route_labels.shape[1] and 0 <= sy < route_labels.shape[0]:
                source = route_labels[sy, sx]
        for (kind, identifier, label), position in candidates:
            if position.shape != (2,) or not np.isfinite(position).all():
                continue
            x, y = np.floor(position).astype(int)
            if not (0 <= x < width and 0 <= y < height) or field[y, x] > ROAD_TOLERANCE:
                continue
            # 分区按原始坐标比较：网格吸附会把相邻分区的点并到同一节点，虚报可达。
            target_label = 0 if route_labels is None else route_labels[y // 8, x // 8]
            if source and target_label and target_label != source:
                continue
            if limit is not None and np.linalg.norm(position - squad) > limit:
                continue
            distance = walking_distance(road, route_labels, squad, position)
            if not np.isfinite(distance) or (limit is not None and distance > limit):
                continue
            if best is not None and distance >= best['distance']:
                continue
            best = dict(kind=kind, position=position.tolist(), distance=distance, id=identifier, label=label)
        return best

    def find(self, snapshot, radius=NEARBY_RADIUS):
        """返回下一个目标，或 None。

        snapshot 需要 accepted 定位、小队坐标和敌人列表；敌人只取普通标记，EX 不进入选择。
        radius 只约束没有敌人时的机关和电梯，敌人以观测范围为准。
        """
        if snapshot.get('localization') != 'accepted' or snapshot.get('squad') is None:
            return None
        squad = np.asarray(snapshot['squad'], float)
        if squad.shape != (2,) or not np.isfinite(squad).all() or not self.supported:
            return None
        try:
            road, route_labels = self._walkable(snapshot, squad)
        except ValueError as exc:
            if not self._surface_warned:
                logger.warning(f'第 {self.chapter} 章暂不能选择目标：{exc}')
                self._surface_warned = True
            return None
        field = cv2.distanceTransform(np.asarray(road <= 0, np.uint8), cv2.DIST_L2, 5)
        enemies = [(('enemy', None, ''), np.asarray(item['position'], float))
                   for item in snapshot.get('enemies', []) if item.get('kind') == 'normal']
        target = self._nearest(field, road, route_labels, squad, enemies, None)
        if target is not None:
            return target
        objects = [((obj['category'], obj['id'], obj.get('label', '')), np.asarray(obj['points'][0], float))
                   for obj in self.annotations.get('objects', [])
                   if obj.get('category') in TARGET_CATEGORIES and obj.get('type') == 'point']
        return self._nearest(field, road, route_labels, squad, objects, radius)
