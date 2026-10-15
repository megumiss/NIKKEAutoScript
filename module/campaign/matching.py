"""Register Wiki minimap screenshots against a chapter package; reject uncertain positions.

正式推图的小地图→底图配准就走这里（`CampaignObserver` 与原型共用同一份 `MapMatcher`），
离线 Wiki 收集品批处理也用它，所以接受判据（ACCEPT_*）改动会同时影响现场定位与离线核对。
"""

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


ROI = (25, 96, 243, 307)
SCALE = 0.25
PAD = 180
MATCH_VERSION = 5
# match_panel 的接受判据：道路 IoU、粗搜首名与远处歧义候选的分差、同分候选的参数离散度。
# 观察器要用同一组常量解释定位为何未被接受，写成常量而不是散在判据里的字面量，避免两边漂移。
# 道路 IoU 取 0.70：小地图只露出一小块路、或该章配色与底图有固有差异时，正确位置实测落在
# 0.71~0.84（ch38 实测 0.832 被 0.85 拒掉），把这些正确位置全判死代价太大；错位与歧义主要由
# margin 与 parameter_spread 两道判据拦截，IoU 只作为「确实压在道路上」的下限。
ACCEPT_ROAD_IOU = .70
ACCEPT_COARSE_MARGIN = .10
ACCEPT_PARAMETER_SPREAD_PX = 20
# match_panel 粗搜索的参数网格；同一个 MapMatcher 实例反复配准时尺度基本不变，先在最近
# 接受的 (a, b) 附近做小网格热启动，命中即跳过全网格，未命中再退回完整扫描。
PARAMETER_GRID = sorted({(round(float(a), 2), round(float(b), 2))
                         for a in np.arange(.6, 2.21, .2) for b in np.arange(.65, 1.56, .15)}
                        | {(round(float(a), 2), round(float(b), 2))
                           for a in np.arange(1.9, 3.21, .1) for b in np.arange(.45, .851, .05)})
# 热启动的小网格半径：a 步长 0.2、b 步长 0.15，各取 ±1 步；超出即视为尺度漂移，退回全扫。
WARM_START_RADIUS = 1


def minimap_masks(image):
    """将支持的横屏截图归一化后裁出紧凑地图，排除细网格、控件及小队标记再提取道路。"""
    height, width = image.shape[:2]
    if abs(width / height - 16 / 9) > 0.12:
        raise ValueError('Unsupported screenshot layout; retain for manual review.')
    normalized = cv2.resize(image, (1920, 1080))
    x, y, right, bottom = ROI
    panel = normalized[y:bottom, x:right]
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    road = cv2.inRange(hsv, (85, 65, 115), (115, 255, 255))
    # Original PNGs retain thin bright grid lines that CDN-compressed previews blur away.
    road = cv2.morphologyEx(road, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    valid = np.full(road.shape, 255, np.uint8)
    valid[:3] = valid[-3:] = 0
    valid[:, :3] = valid[:, -3:] = 0
    valid[:29, :42] = valid[:29, -34:] = valid[-31:, -76:] = 0
    bright = (hsv[:, :, 2] > 225).astype(np.uint8) * 255
    bright[72:130, 78:138] = 0
    valid[cv2.dilate(bright, np.ones((15, 15), np.uint8)) > 0] = 0
    player = player_center(panel)
    cv2.circle(valid, tuple(np.rint(player).astype(int)), 21, 0, -1)
    road[valid == 0] = 0
    if np.count_nonzero(road) < 200:
        raise ValueError('Too little minimap road evidence.')
    return panel, road, valid, player


def player_center(panel):
    """在预期区域用三点 RANSAC 与最小二乘拟合小队圆环，不用固定截图中心代替物品位置。"""
    white = cv2.inRange(cv2.cvtColor(panel, cv2.COLOR_BGR2HSV), (0, 0, 145), (179, 90, 255))
    white[:74] = white[134:] = white[:, :78] = white[:, 143:] = 0
    ys, xs = np.nonzero(white)
    points = np.column_stack([xs, ys]).astype(float)
    if len(points) < 25:
        raise ValueError('Squad ring is absent from the expected minimap region.')
    rng = np.random.default_rng(40)
    best = None
    for _ in range(1800):
        q = points[rng.choice(len(points), 3, replace=False)]
        matrix = 2 * (q[1:] - q[0])
        if abs(np.linalg.det(matrix)) < 1:
            continue
        center = np.linalg.solve(matrix, (q[1:] ** 2).sum(axis=1) - (q[0] ** 2).sum())
        radius = np.linalg.norm(q[0] - center)
        if not (9 < radius < 19 and 88 < center[0] < 127 and 85 < center[1] < 124):
            continue
        keep = np.abs(np.linalg.norm(points - center, axis=1) - radius) < 1.4
        angles = np.arctan2((points - center)[keep, 1], (points - center)[keep, 0])
        bins = len(np.unique(np.floor((angles + np.pi) * 16 / (2 * np.pi))))
        score = int(keep.sum()) + bins * 2
        if bins >= 11 and (best is None or score > best[0]):
            best = score, keep
    if best is None:
        raise ValueError('No reliable squad ring; do not use a fixed screenshot center.')
    keep = best[1]
    for _ in range(3):
        q = points[keep]
        if len(q) < 25:
            raise ValueError('Insufficient squad ring support.')
        sol = np.linalg.lstsq(np.column_stack([2 * q, np.ones(len(q))]), (q * q).sum(axis=1), rcond=None)[0]
        center = sol[:2]
        radius = np.sqrt(max(0, sol[2] + (center * center).sum()))
        keep = np.abs(np.linalg.norm(points - center, axis=1) - radius) < 1.4
    if not (9 < radius < 19 and 88 < center[0] < 127 and 85 < center[1] < 124):
        raise ValueError('Refined squad ring is outside expected bounds.')
    return center


def template_matrix(projection, a, b, shape):
    """枚举尺度与透视组合，将紧凑地图映射到地图投影平面并返回有效模板边界。"""
    height, width = shape
    transform = np.array([[a, 0, 243 - a * width / 2], [0, a, 231 - a * height / 2], [0, 0, 1.]])
    matrix = np.diag([b * SCALE, b * SCALE, 1.]) @ projection @ transform
    corners = cv2.perspectiveTransform(np.array([[[0., 0], [width, 0], [width, height], [0, height]]]), matrix)[0]
    lower = np.floor(corners.min(axis=0))
    size = np.ceil(corners.max(axis=0) - lower).astype(int)
    if not np.isfinite(corners).all() or min(size) < 8 or max(size) > 600:
        return None
    matrix = np.array([[1, 0, -lower[0]], [0, 1, -lower[1]], [0, 0, 1.]]) @ matrix
    return matrix, tuple(int(v) for v in size)


def iou_surface(target, road, valid, matrix, size):
    """在有效模板域计算所有平移候选的道路交并比，排除模板超界或道路证据过少的情况。"""
    if size[0] > target.shape[1] or size[1] > target.shape[0]:
        return None
    r = cv2.warpPerspective(road, matrix, size, flags=cv2.INTER_LINEAR).astype(np.float32) / 255
    v = cv2.warpPerspective(valid, matrix, size, flags=cv2.INTER_NEAREST).astype(np.float32) / 255
    r *= v
    if r.sum() < 30:
        return None
    intersection = cv2.matchTemplate(target, r, cv2.TM_CCORR)
    total = cv2.matchTemplate(target, v, cv2.TM_CCORR)
    return np.clip(intersection / np.maximum(total + r.sum() - intersection, 1), 0, 1)


class MapMatcher:
    """把小地图面板配准到章节底图。

    平面章节在这里自己做粗搜索加全分辨率搜索。底图哈希必须与元数据一致，
    否则配准结果对应的是另一张图。
    """

    def __init__(self, package):
        """核对底图哈希、配准状态和缓存投影，再构建粗搜索与全分辨率搜索画布。"""
        self.package = Path(package)
        meta = json.loads((self.package / 'map.json').read_text(encoding='utf-8'))
        self.digest = hashlib.sha256((self.package / 'map.png').read_bytes()).hexdigest()
        if meta['image_sha256'] != self.digest:
            raise ValueError('Map hash differs from metadata.')
        if meta.get('coordinate_model') is not None:
            raise ValueError('只支持平面地图包。')
        if meta.get('capture', {}).get('registration', {}).get('status') != 'joint_grid_road':
            raise ValueError('Map package lacks accepted road registration.')
        self.projection = np.asarray(meta['transforms']['projection'], float)
        cache_path = self.package / 'source/map_data.npz'
        self.cache_digest = hashlib.sha256(cache_path.read_bytes()).hexdigest()
        with np.load(cache_path) as cache:
            self.terrain = (cache['terrain_probability'] >= .5).astype(np.float32)
            if not np.allclose(cache['projection'], self.projection):
                raise ValueError('Cached projection differs from map metadata.')
        from module.campaign_prototype.edited_map import edited_roads
        self.terrain, _, edits_digest = edited_roads(self.package, meta, self.terrain)
        self.terrain = self.terrain.astype(np.float32)
        self.cache_digest = hashlib.sha256((self.cache_digest + edits_digest).encode()).hexdigest()
        if list(self.terrain.shape[::-1]) != meta['size']:
            raise ValueError('Cached terrain dimensions differ from the map.')
        self.target = cv2.copyMakeBorder(cv2.resize(self.terrain, None, fx=SCALE, fy=SCALE,
                                                  interpolation=cv2.INTER_AREA),
                                        PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT, value=0)
        self.pad = 900
        self.large = cv2.copyMakeBorder(self.terrain, self.pad, self.pad, self.pad, self.pad,
                                       cv2.BORDER_CONSTANT, value=0)
        self.warm_parameters = None
        self.warm_margin = None

    def match(self, image, review=None):
        """粗搜索尺度和位移后细化最佳候选，以 IoU、远处候选分差及参数分散度联合决定是否接受。"""
        cropped = None
        try:
            panel, road, valid, player = minimap_masks(image)
        except ValueError:
            from module.campaign.minimap import cropped_minimap
            panel, road, valid, player, cropped = cropped_minimap(image)
        result = self.match_panel(panel, road, valid, player, review)
        if cropped is not None:
            x, y, right, bottom = cropped
            scale = np.array([panel.shape[1] / (right - x), panel.shape[0] / (bottom - y)])
            result.update(roi=list(cropped), normalized_size=list(image.shape[1::-1]),
                          player_center=(player / scale).tolist(), screenshot_layout='cropped_minimap')
            if result.get('roi_to_map') is not None:
                result['roi_to_map'] = (np.asarray(result['roi_to_map']) @ np.diag([*scale, 1.])).tolist()
        return result

    def match_panel(self, panel, road, valid, player, review=None):
        """已裁剪小地图的道路搜索。

        粗搜索优先在最近一次 accepted 配准的 (a, b) 附近做小网格热启动：热启动候选分达标的
        参数若与缓存一致则复用当时的粗搜 margin，可继续给出 accepted；命中失败或候选分过低
        退回全网格扫描，行为与无缓存一致。同一实例在移动闭环里反复配准，尺度几乎不变，
        热启动能省九成以上的 matchTemplate。
        """
        coarse = []
        # 候选分门槛取 0.6：粗搜网格 IoU 明显低于接受线（全分辨率细化后要求 ACCEPT_ROAD_IOU）的参数
        # 不会成为最终候选，热启动时不值得为它们做完全网格。
        warm_score_floor = .6

        def sweep(parameters):
            """对给定 (a, b) 参数做一次粗搜打分，返回粗搜候选列表。"""
            found = []
            for a, b in sorted(parameters):
                template = template_matrix(self.projection, a, b, road.shape)
                if template is None:
                    continue
                matrix, size = template
                surface = iou_surface(self.target, road, valid, matrix, size)
                if surface is None:
                    continue
                _, score, _, location = cv2.minMaxLoc(surface)
                cv2.circle(surface, location, round(130 * SCALE), 0, -1)
                _, second, _, second_location = cv2.minMaxLoc(surface)
                p = cv2.perspectiveTransform(player[None, None], matrix)[0, 0]
                found.append({'a': a, 'b': b, 'score': score, 'second': second,
                              'position': (np.asarray(location) + p - PAD) / SCALE,
                              'second_position': (np.asarray(second_location) + p - PAD) / SCALE,
                              'location': location, 'matrix': matrix, 'size': size})
            return found

        # seed_matcher 等旁路构造的实例没有热启动字段；热启动只对持有完整画布的实例有意义。
        warm = getattr(self, 'warm_parameters', None)
        warm_hit = False
        if warm is not None:
            steps = sorted({b for _, b in PARAMETER_GRID})
            neighborhood = {(a, b) for a, b in PARAMETER_GRID
                            if abs(steps.index(b) - steps.index(warm[1])) <= WARM_START_RADIUS
                            and abs(a - warm[0]) <= .21 * (WARM_START_RADIUS + 1) + 1e-9}
            coarse = sweep(neighborhood)
            # 热启动候选分明显低于接受线说明尺度漂移了，退回全网格而不是拿坏候选继续。
            warm_hit = bool(coarse) and max(item['score'] for item in coarse) >= warm_score_floor
            if not warm_hit:
                coarse = sweep(PARAMETER_GRID)
        else:
            coarse = sweep(PARAMETER_GRID)
        if not coarse:
            raise ValueError('No usable registration candidates.')
        coarse.sort(key=lambda r: r['score'], reverse=True)
        candidates = []
        for seed in coarse[:8]:
            full = np.diag([1 / SCALE, 1 / SCALE, 1.]) @ seed['matrix']
            size = tuple(round(v / SCALE) for v in seed['size'])
            predicted = np.asarray(seed['location']) / SCALE - PAD / SCALE
            start = np.rint(predicted).astype(int) + self.pad - 20
            if min(start) < 0:
                continue
            patch = self.large[start[1]:start[1] + size[1] + 40, start[0]:start[0] + size[0] + 40]
            surface = iou_surface(patch, road, valid, full, size)
            if surface is None:
                continue
            _, score, _, location = cv2.minMaxLoc(surface)
            offset = start + np.asarray(location) - self.pad
            matrix = np.array([[1, 0, offset[0]], [0, 1, offset[1]], [0, 0, 1.]]) @ full
            point = cv2.perspectiveTransform(player[None, None], matrix)[0, 0]
            candidates.append({'road_iou': score, 'position': point.tolist(), 'roi_to_map': matrix.tolist(),
                               'a': seed['a'], 'b': seed['b']})
        if not candidates:
            raise ValueError('No full-resolution registration candidate fits the map.')
        result = max(candidates, key=lambda r: r['road_iou'])
        point = np.asarray(result['position'])
        alternatives = [r['score'] for r in coarse if np.linalg.norm(r['position'] - point) > 130]
        alternatives += [r['second'] for r in coarse if np.linalg.norm(r['second_position'] - point) > 130]
        margin = coarse[0]['score'] - max(alternatives, default=0)
        spread = max(np.linalg.norm(np.asarray(r['position']) - point) for r in candidates
                     if r['road_iou'] >= result['road_iou'] - .03)
        inside = np.all(point >= 0) and np.all(point <= np.asarray(self.terrain.shape[::-1]) - 1)
        # margin 是「粗搜首名与远处歧义候选的分差」，热启动只扫了参数邻域、语义不完整；
        # 但该分差主要由尺度参数决定，与平移无关，所以热启动命中的参数若正是上次全网格
        # accepted 的参数，可以直接复用当时的 margin 得出 accepted，避免每帧都做全网格。
        if warm_hit and self.warm_parameters == (result['a'], result['b']) and self.warm_margin is not None:
            margin = self.warm_margin
        accepted = bool(inside and result['road_iou'] >= ACCEPT_ROAD_IOU and margin >= ACCEPT_COARSE_MARGIN
                        and spread <= ACCEPT_PARAMETER_SPREAD_PX)
        result.update(status='accepted' if accepted else 'needs_review', coarse_margin=float(margin),
                      parameter_spread_px=float(spread), roi=list(ROI), player_center=player.tolist(),
                      normalized_size=[1920, 1080], image_sha256=self.digest, match_version=MATCH_VERSION)
        # 只有全网格 accepted 才更新热启动：needs_review 的定位不可信，把它的参数喂给下一帧
        # 会把漂移延续下去；accepted 复用同一参数直接命中，后续帧全部走小网格并复用 margin。
        if accepted and not warm_hit and hasattr(self, 'warm_parameters'):
            self.warm_parameters = (result['a'], result['b'])
            self.warm_margin = float(margin)
        if review is not None:
            recovered = cv2.warpPerspective((self.terrain * 255).astype(np.uint8),
                                            np.linalg.inv(np.asarray(result['roi_to_map'])), panel.shape[1::-1])
            overlay = panel.copy()
            contours, _ = cv2.findContours(recovered, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, (0, 255, 255), 1)
            cv2.drawMarker(overlay, tuple(np.rint(player).astype(int)), (0, 215, 255), cv2.MARKER_CROSS, 14, 1)
            if not cv2.imwrite(str(review), np.hstack([panel, overlay])):
                raise OSError(f'Cannot save alignment review: {review}')
        return result
