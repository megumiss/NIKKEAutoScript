"""推图期间的地图信息。

静态层 `StaticMap` 按章节加载一次，只回答三个问题：这个点能不能站、两点是否连通、某类目标有哪些候选。
观测层就是 `CampaignObserver.observe` 产出的快照，每帧整体替换；这里只提供读取它所需的表面归属，不累积历史。
"""

import json
from pathlib import Path

import cv2
import numpy as np

from dev_tools.map_annotator import COORDINATES, validate_annotations
from dev_tools.map_connectivity import decode_labels, ensure_data, package_road
from module.campaign_prototype.local_projection import frame_plane_to_map
from module.campaign_prototype.parallax_movement import surface_identity, surface_union

# 标记中心和人工标注都会偏出道路几个像素，按最近的道路像素归属分量。
ROAD_TOLERANCE = 12
# 连通分区是在 8 像素网格上计算的（dev_tools/map_connectivity.py），分量查询按同一网格采样。
GRID = 8
# 动态封锁：局部道路方向的估计窗口半径，以及切口长度的下限、上限和外扩余量。
BLOCK_WINDOW = 60
BLOCK_MIN = 24
BLOCK_MAX = 160
BLOCK_MARGIN = 12

TARGET_CATEGORIES = ('ground_mechanism', 'ground_elevator', 'normal_collectible', 'hard_collectible')


def point_category(item):
    """标注类别；旧的 Wiki 收集品只有难度没有 category，按难度归类。"""
    category = item.get('category')
    if category:
        return category
    difficulty = item.get('difficulty') or item.get('source', {}).get('difficulty')
    return f'{difficulty}_collectible' if difficulty in ('normal', 'hard') else None


class StaticMap:
    """章节地图的只读信息，与 MapMatcher 共享同一份底图与道路修订。

    平面地图的连通性来自 connectivity 的 effective 分区；分层地图按小队所在表面的道路连通分量判断，
    表面之间不互通。两种模型都只在道路附近归属分量，远离道路的点视为不可站。
    `navigation.TargetFinder` 从这里读道路、分区、标注与同层掩码，不再自行加载。
    """

    def __init__(self, package, matcher):
        self.package = Path(package)
        metadata = json.loads((self.package / 'map.json').read_text(encoding='utf-8'))
        self.chapter = int(metadata['chapter'])
        self.model = metadata.get('coordinate_model')
        self.size = tuple(int(v) for v in metadata['size'])
        self.image_sha256 = matcher.digest
        self.localizer = matcher.surface_matcher
        self.supported = self.model in (None, 'local_parallax')
        self.annotations = self._document(matcher.digest, metadata['size'])
        self.road = None
        self.regions = None
        if self.model is None:
            road = package_road(self.package, metadata, self.annotations)
            connectivity = ensure_data(self.package, metadata, self.annotations)
            self.road = road.astype(np.uint8)
            self.regions = decode_labels(connectivity['effective'], road[::GRID, ::GRID].shape)
        elif self.model == 'local_parallax':
            self.road = self.localizer.road.astype(np.uint8)
        self._components = {}
        self.points = self._points()
        self.transfers = self._transfers()
        # 每帧观测替换的动态道路封锁（如光幕），只用于展示，不改写道路与连通分区。
        self.blocks = []

    def _snap_road(self, point, tolerance=ROAD_TOLERANCE):
        """最近的道路像素坐标；点远离道路时返回 None。"""
        point = np.asarray(point, float)
        if point.shape != (2,) or not np.isfinite(point).all():
            return None
        radius = int(np.ceil(tolerance))
        x, y = int(np.floor(point[0])), int(np.floor(point[1]))
        height, width = self.road.shape
        x0, y0 = max(0, x - radius), max(0, y - radius)
        x1, y1 = min(width, x + radius + 1), min(height, y + radius + 1)
        if x0 >= x1 or y0 >= y1:
            return None
        ys, xs = np.nonzero(self.road[y0:y1, x0:x1])
        if not len(xs):
            return None
        distance = np.hypot(xs + x0 - point[0], ys + y0 - point[1])
        nearest = int(np.argmin(distance))
        if distance[nearest] > tolerance:
            return None
        return np.array([xs[nearest] + x0, ys[nearest] + y0], float)

    def mark_barrier(self, point, direction=None, tolerance=ROAD_TOLERANCE, span=None):
        """把一处屏障标记为道路封锁：吸附到最近道路后沿屏障方向切开，返回封锁段或 None。

        direction 为屏障两根立柱连线的地图向量；缺失时按局部道路主方向的垂线估计。
        span 为立柱间距时切口长度按屏障物理宽度外加余量；否则取窗口内道路沿封锁方向的
        跨度外加余量。长度都限制在 BLOCK_MIN～BLOCK_MAX。
        场景投影误差大于人工标注偏移，屏障落图应传更大的 tolerance。
        """
        snapped = self._snap_road(point, tolerance)
        if snapped is None:
            return None
        x, y = (int(v) for v in snapped)
        top, left = max(0, y - BLOCK_WINDOW), max(0, x - BLOCK_WINDOW)
        ys, xs = np.nonzero(self.road[top:y + BLOCK_WINDOW + 1, left:x + BLOCK_WINDOW + 1])
        if len(xs) < 10:
            return None
        local = np.column_stack((xs, ys)).astype(float) + [left, top]
        if direction is None:
            # 道路沿走向拉长，协方差最小方向即横跨道路的封锁方向。
            covariance = np.cov((local - snapped).T)
            across = np.linalg.eigh(covariance)[1][:, 0]
        else:
            across = np.asarray(direction, float)
        norm = np.linalg.norm(across)
        if norm < 1e-6:
            return None
        across = across / norm
        extent = np.ptp((local - snapped) @ across)
        length = float(np.clip(extent + 2 * BLOCK_MARGIN, BLOCK_MIN, BLOCK_MAX))
        ends = [snapped - across * length / 2, snapped + across * length / 2]
        return dict(point=[round(float(v), 1) for v in snapped],
                    segment=[[round(float(v), 1) for v in end] for end in ends],
                    snap=round(float(np.linalg.norm(snapped - np.asarray(point, float))), 1))

    def _document(self, image_hash, size):
        path = self.package / 'annotations.json'
        if path.exists():
            document = json.loads(path.read_text(encoding='utf-8'))
        else:
            document = {'schema_version': 1, 'image': 'map.png', 'image_sha256': image_hash,
                        'coordinates': COORDINATES.copy(), 'objects': [], 'connections': []}
        validate_annotations(document, image_hash, size)
        return document

    def _points(self):
        """点状标注；折线和多边形没有唯一目标点，不进入候选。"""
        result = []
        for item in self.annotations.get('objects', []):
            category = point_category(item)
            if category not in TARGET_CATEGORIES or item.get('type') != 'point':
                continue
            result.append(dict(id=item['id'], label=item.get('label', ''), category=category,
                               position=[float(v) for v in item['points'][0]],
                               surface_id=item.get('surface_id')))
        return result

    def _transfers(self):
        """电梯传送关系，端点换成坐标；只收录两端都是点状电梯的连接。"""
        points = {item['id']: item for item in self.points if item['category'] == 'ground_elevator'}
        result = []
        for link in self.annotations.get('connections', []):
            if link.get('category') != 'elevator_connection':
                continue
            if link['from'] not in points or link['to'] not in points:
                continue
            result.append(dict(id=link['id'], entry=points[link['from']]['position'],
                               exit=points[link['to']]['position'], directed=bool(link['directed'])))
        return result

    def targets(self, *categories):
        """某类目标的候选；不传类别时返回全部点状标注。"""
        if not categories:
            return list(self.points)
        return [item for item in self.points if item['category'] in categories]

    def surface_key(self, snapshot):
        """观测所在表面的缓存键；平面地图为 None，分层地图没有表面信息时抛错。"""
        if self.model is None:
            return None
        surface = snapshot.get('surface')
        if not surface:
            raise ValueError('定位结果没有表面信息，无法限定同层道路。')
        identity = surface.get('id')
        if identity is None:
            identity = surface_identity(self.localizer, surface)
        if identity is not None:
            return int(identity)
        return ('frame', int(surface['reference_frame']), int(surface['local_surface']))

    def _labels(self, key):
        """分量标签图：平面地图按网格分区展开到像素；分层地图按表面道路的连通分量。"""
        if key in self._components:
            return self._components[key]
        if self.model is None:
            labels = np.repeat(np.repeat(self.regions, GRID, axis=0), GRID, axis=1)
            labels = labels[:self.size[1], :self.size[0]].astype(np.int32)
            labels[self.road == 0] = 0
        else:
            metadata = self.localizer.metadata
            if isinstance(key, tuple):
                _, frame, label = key
                plane = metadata['frame_support'][frame]['local_planes'][label]
                mask = cv2.warpPerspective((self.localizer.references[frame]['labels'] == label).astype(np.uint8),
                                           frame_plane_to_map(metadata, frame, plane), self.size,
                                           flags=cv2.INTER_NEAREST)
            else:
                mask = surface_union(self.localizer, key).copy()
            mask &= self.road
            _, labels = cv2.connectedComponents(mask)
        self._components[key] = labels
        return labels

    def component_mask(self, key, point):
        """包含该点所在像素的同层道路连通分量；点不在本层道路上时抛错，与原型 surface_mask 的语义一致。"""
        labels = self._labels(key)
        x, y = np.rint(np.asarray(point, float)).astype(int)
        if not (0 <= x < labels.shape[1] and 0 <= y < labels.shape[0]) or not labels[y, x]:
            raise ValueError('小队位置缺少本层道路证据。')
        return (labels == labels[y, x]).astype(np.uint8)

    def component(self, point, key=None, tolerance=ROAD_TOLERANCE):
        """点所在的连通分量编号，远离道路或落在分隔带上返回 0。

        以最近的、带分量的道路像素为准：标记中心常偏出道路几个像素，而分隔带内的像素没有分量。
        """
        point = np.asarray(point, float)
        if point.shape != (2,) or not np.isfinite(point).all():
            return 0
        labels = self._labels(key)
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

    def walkable(self, point, key=None):
        return self.component(point, key) != 0

    def connected(self, start, target, key=None):
        """两点是否在同一连通分量；分层地图的 key 指明小队所在表面。"""
        home = self.component(start, key)
        return home != 0 and self.component(target, key) == home
