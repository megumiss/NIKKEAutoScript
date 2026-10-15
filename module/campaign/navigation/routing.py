"""按光幕封锁切开的派生路网：推图的可达性与路径都走切开后的图。

静态层 `StaticMap` 的道路与分区是地图本身的事实，不随光幕变化，所以不动它；这里按运行状态里
仍然算挡路的封锁（active 与 stale）切出一份派生道路与分区，只在封锁集合变化时重算。
两张图都留着是为了回答一个关键问题：**目标在原图可达、在切开图不可达，就说明它是被光幕挡住的**，
那是去踩机关解封的信号，而不是「这里根本走不通」。
切口几何与人工分隔线同源（`connectivity.build` 的 effective 分支）：先对道路做 3×3 开运算去噪点，
再把封锁段按固定宽度画成实线抹掉，最后按八邻域取连通分量并抽到 8 像素网格。
"""

import cv2
import numpy as np
from PIL import Image, ImageDraw

from module.campaign.map.map_state import GRID, ROAD_TOLERANCE

# 切口线宽（地图像素）：封锁段本身已横跨道路，线宽只需保证八邻域不从斜角漏过去。
# 取 16 而不是更细，是因为底图道路边缘有锯齿，过细的切口会留下一两像素的斜向通路。
CUT_WIDTH = 16
# 复核站位到封锁点的目标距离：紧凑小地图向前能看约 400 底图像素，站在 120 处时光幕稳稳在视野内，
# 离面板边界的余量也远大于 RunState 判「该看见」用的 24 像素。
STANDOFF = 120.
# 实际可用的站位距离区间（相对 STANDOFF 的倍数）：道路不一定正好在 120 处有落点。
STANDOFF_BAND = (.6, 1.4)
# 逐个撤销试可达的封锁数上限：每次试算要重做一遍连通分量，封锁通常只有一两块。
CULPRIT_TRIALS = 3


def component_at(labels, point, tolerance=ROAD_TOLERANCE):
    """点所在的连通分量编号，远离道路或落在切口上返回 0。

    判据与 `StaticMap.component` 一致：以最近的、带分量的道路像素为准，容差同为 ROAD_TOLERANCE。
    标记中心和投影落点常偏出道路几个像素，直接取像素值会把它们判成不可站。
    """
    point = np.asarray(point, float)
    if point.shape != (2,) or not np.isfinite(point).all():
        return 0
    x, y = int(np.floor(point[0])), int(np.floor(point[1]))
    height, width = labels.shape
    x0, y0 = max(0, x - tolerance), max(0, y - tolerance)
    x1, y1 = min(width, x + tolerance + 1), min(height, y + tolerance + 1)
    if x0 >= x1 or y0 >= y1:
        return 0
    window = labels[y0:y1, x0:x1]
    ys, xs = np.nonzero(window)
    if not len(xs):
        return 0
    distance = np.hypot(xs + x0 - point[0], ys + y0 - point[1])
    nearest = int(np.argmin(distance))
    if distance[nearest] > tolerance:
        return 0
    return int(window[ys[nearest], xs[nearest]])


class CutRoads:
    """按封锁切开的路网视图：道路掩码、8 像素分区与可达性查询。

    一份视图绑定一章的 `StaticMap`。封锁集合用 (id, 切口端点) 做签名，签名不变就复用上次的结果：
    推图每拍都要查可达性，但封锁很少变，没必要每拍重算连通分量。
    """

    def __init__(self, chart):
        """记下静态层并置空派生结果，首次 update 时才真正算。"""
        self.chart = chart
        self.blocks = []
        self.signature = None
        self.road = None
        self.labels = None
        self.regions = None

    @staticmethod
    def _signature(blocks):
        """封锁集合的签名：编号加切口端点，端点取整到像素。

        只用于判断「要不要重算」：亚像素的抖动不值得重做一遍连通分量。画线仍用原始浮点端点，
        这样切出来的掩码与 `connectivity.build` 对同一份端点的结果逐像素一致。
        """
        items = []
        for record in blocks:
            segment = record.get('segment')
            if not segment:
                continue
            items.append((record['id'], tuple(tuple(np.rint(point).astype(int)) for point in segment)))
        return tuple(sorted(items))

    @staticmethod
    def _segments(blocks):
        """按编号排序的切口端点（原始浮点），画线顺序与签名一致。"""
        items = []
        for record in blocks:
            segment = record.get('segment')
            if not segment:
                continue
            items.append((record['id'], [tuple(float(value) for value in point) for point in segment]))
        return [segment for _, segment in sorted(items, key=lambda item: item[0])]

    def _cut_mask(self, blocks):
        """在开运算后的道路上抹掉所有切口，返回切开后的二值道路。"""
        mask = cv2.morphologyEx(self.chart.road.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        segments = self._segments(blocks)
        if not segments:
            return mask
        canvas = Image.new('L', self.chart.size, 0)
        draw = ImageDraw.Draw(canvas)
        for segment in segments:
            draw.line(segment, fill=255, width=CUT_WIDTH, joint='curve')
        mask = mask.copy()
        mask[np.asarray(canvas) > 0] = 0
        return mask

    def update(self, blocks):
        """按当前仍算挡路的封锁刷新派生路网；签名未变时直接复用，返回是否重算。"""
        signature = self._signature(blocks)
        if signature == self.signature and self.road is not None:
            return False
        self.blocks = list(blocks)
        self.signature = signature
        self.road = self._cut_mask(self.blocks)
        self.labels = cv2.connectedComponents(self.road, connectivity=8)[1]
        self.regions = self.labels[::GRID, ::GRID]
        return True

    def component(self, point):
        """点在切开图上的连通分量编号。"""
        return component_at(self.labels, point)

    def walkable(self, point):
        """这个点在切开图上还能不能站。"""
        return self.component(point) != 0

    def connected(self, start, target):
        """两点在切开图上是否连通。"""
        home = self.component(start)
        return home != 0 and self.component(target) == home

    def component_mask(self, point):
        """包含该点的切开图连通分量掩码；点不在道路上时抛错，与 StaticMap.component_mask 同语义。"""
        home = self.component(point)
        if not home:
            raise ValueError('该位置在切开后的路网上缺少道路证据。')
        return (self.labels == home).astype(np.uint8)

    def blocked(self, start, target):
        """目标是否「原图可达但切开图不可达」——即只被光幕挡住。"""
        return self.chart.connected(start, target) and not self.connected(start, target)

    def culprits(self, start, target):
        """挑出拦在这条路上的封锁编号：逐个撤掉它再试，撤掉就通的那块就是拦路的。

        按到两端连线的距离排序后只试前 CULPRIT_TRIALS 块，避免封锁多时反复重算连通分量。
        返回空列表说明不是单块封锁造成的（多块同时拦，或根本不是封锁的问题）。
        """
        if not self.blocks:
            return []
        start, target = np.asarray(start, float), np.asarray(target, float)
        axis = target - start
        length = float(np.linalg.norm(axis))

        def offset(record):
            """封锁点到起点—目标连线的距离，用来优先试最可能拦路的那块。"""
            point = np.asarray(record['point'], float)
            if length < 1e-6:
                return float(np.linalg.norm(point - start))
            return float(abs(np.cross(axis, point - start)) / length)

        result = []
        for record in sorted(self.blocks, key=offset)[:CULPRIT_TRIALS]:
            rest = [item for item in self.blocks if item['id'] != record['id']]
            labels = cv2.connectedComponents(self._cut_mask(rest), connectivity=8)[1]
            home = component_at(labels, start)
            if home and component_at(labels, target) == home:
                result.append(record['id'])
        return result

    def standoff(self, block, squad):
        """封锁的复核站位：小队这一侧、离封锁点约 STANDOFF 的道路落点；找不到返回 None。

        必须取小队同侧：站到光幕另一侧既走不过去，复核也没有意义。同侧判据是切开图上同一分量。
        在允许的距离区间里取离小队最近的落点，少走冤枉路。
        """
        point = np.asarray(block.get('point'), float)
        squad = np.asarray(squad, float)
        if point.shape != (2,) or not np.isfinite(point).all():
            return None
        home = self.component(squad)
        if not home:
            return None
        low, high = STANDOFF * STANDOFF_BAND[0], STANDOFF * STANDOFF_BAND[1]
        radius = int(np.ceil(high))
        x, y = int(point[0]), int(point[1])
        height, width = self.labels.shape
        x0, y0 = max(0, x - radius), max(0, y - radius)
        x1, y1 = min(width, x + radius + 1), min(height, y + radius + 1)
        window = self.labels[y0:y1, x0:x1]
        ys, xs = np.nonzero(window == home)
        if not len(xs):
            return None
        points = np.column_stack((xs + x0, ys + y0)).astype(float)
        spans = np.linalg.norm(points - point, axis=1)
        keep = (spans >= low) & (spans <= high)
        if not keep.any():
            return None
        candidates = points[keep]
        nearest = candidates[np.argmin(np.linalg.norm(candidates - squad, axis=1))]
        return nearest.tolist()
