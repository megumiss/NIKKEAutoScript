"""Register Wiki minimap screenshots against a chapter package; reject uncertain positions.

正式推图的小地图→底图配准就走这里（`CampaignObserver` 与原型共用同一份 `MapMatcher`），
离线 Wiki 收集品批处理也用它，所以接受判据（ACCEPT_*）改动会同时影响现场定位与离线核对。
"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import cv2
import numpy as np


ROI = (25, 96, 243, 307)
SCALE = 0.25
PAD = 180
MATCH_VERSION = 6
# match_panel 的接受判据：道路 IoU、粗搜首名与远处歧义候选的分差、同分候选的参数离散度。
# 观察器要用同一组常量解释定位为何未被接受，写成常量而不是散在判据里的字面量，避免两边漂移。
# 道路 IoU 取 0.70：小地图只露出一小块路、或该章配色与底图有固有差异时，正确位置实测落在
# 0.71~0.84（ch38 实测 0.832 被 0.85 拒掉），把这些正确位置全判死代价太大；错位与歧义主要由
# margin 与 parameter_spread 两道判据拦截，IoU 只作为「确实压在道路上」的下限。
ACCEPT_ROAD_IOU = .70
ACCEPT_COARSE_MARGIN = .10
ACCEPT_PARAMETER_SPREAD_PX = 20
# 已确认小队坐标对展开面板配准的约束半径，单位为章节底图像素。
SQUAD_ANCHOR_RADIUS = 12.
# match_panel 粗搜索的参数网格；同一个 MapMatcher 实例反复配准时尺度基本不变，先在最近
# 接受的 (a, b) 附近做小网格热启动，命中即跳过全网格，未命中再退回完整扫描。
PARAMETER_GRID = sorted({(round(float(a), 2), round(float(b), 2))
                         for a in np.arange(.6, 2.21, .2) for b in np.arange(.65, 1.56, .15)}
                        | {(round(float(a), 2), round(float(b), 2))
                           for a in np.arange(1.9, 3.21, .1) for b in np.arange(.45, .851, .05)})
# 热启动的小网格半径：a 步长 0.2、b 步长 0.15，各取 ±1 步；超出即视为尺度漂移，退回全扫。
WARM_START_RADIUS = 1
# 连续帧在上一视口中心附近 ±160 底图像素内搜索，留出细化的 20 像素护栏；贴边即全图复核。
LOCAL_SEARCH_RADIUS = 160
LOCAL_REFINE_GUARD = 20
# 局部搜索无法复核视野外的重复道路，最多连续接受八帧后重新进行全图平移搜索。
LOCAL_SEARCH_MAX_STEPS = 8
# 各配准器共享候选池；按可用逻辑 CPU 的一半预算，给 OpenCV 内部并行与业务线程保留余量。
_SEARCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, (getattr(os, 'process_cpu_count', os.cpu_count)() or 1) // 2),
    thread_name_prefix='map-match',
)


def search_candidates(function, candidates):
    """并行计算独立候选，按输入顺序收集结果，保持同分候选的选择顺序。

    所有任务只读地图与本帧掩码，各自创建分数图；缓存更新仍在调用线程执行。任一任务失败时
    先等待本批其他任务退出再传播异常，避免失败回退或输入释放后仍有上一批任务读取数组。
    """
    futures = [_SEARCH_EXECUTOR.submit(function, candidate) for candidate in candidates]
    try:
        return [future.result() for future in futures]
    finally:
        wait(futures)


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
        from module.campaign.map.edited_map import edited_roads
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
        self.warm_shape = None
        self.warm_center = None
        self.local_parameters = None
        self.local_margin = None
        self.local_steps = 0

    def match(self, image, review=None):
        """粗搜索尺度和位移后细化最佳候选，以 IoU、远处候选分差及参数分散度联合决定是否接受。"""
        # Wiki 图片之间没有时序连续性，不能把上一张图的位置当作本张的局部搜索先验。
        self.warm_center, self.local_steps = None, 0
        cropped = None
        try:
            panel, road, valid, player = minimap_masks(image)
        except ValueError:
            from module.campaign.vision.minimap import cropped_minimap
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

    def match_panel(self, panel, road, valid, player, review=None, expected_position=None):
        """连续帧先局部定位，失败后全图搜索平移，尺度仍不可信时最多补一次全参数搜索。

        面板尺寸变化、显式小队坐标约束和定位失败都会清除位置先验。局部结果必须沿用已验证的
        尺度参数、通过原接受门限且远离窗口边界；连续局部命中达到上限后恢复全图平移搜索。
        局部失配不等于尺度失配，先保留已确认的尺度网格在全图找位，仍失败才丢弃尺度缓存；
        回退不拖动、不重取图，最终失败仍交给调用方恢复。
        """
        shape = tuple(panel.shape[:2])
        if getattr(self, 'warm_shape', None) != shape:
            self.warm_parameters, self.warm_margin = None, None
            self.warm_center, self.local_steps = None, 0
            self.warm_shape = shape
        if expected_position is not None:
            self.warm_center, self.local_steps = None, 0
        had_warm = expected_position is None and getattr(self, 'warm_parameters', None) is not None
        local = (had_warm and getattr(self, 'warm_center', None) is not None
                 and getattr(self, 'local_steps', 0) < LOCAL_SEARCH_MAX_STEPS)
        if local:
            try:
                result = self._match_panel(panel, road, valid, player, review, expected_position, local=True)
            except (ValueError, cv2.error):
                result = None
            if result is not None and result['status'] == 'accepted':
                self._remember_center(panel, result)
                return result
            self.warm_center, self.local_steps = None, 0
        try:
            result = self._match_panel(panel, road, valid, player, review, expected_position)
        except (ValueError, cv2.error):
            self.warm_center, self.local_steps = None, 0
            if not had_warm:
                raise
        else:
            if local:
                result['local_search_retry'] = True
            if result['status'] == 'accepted' or not had_warm or result['search_scope'] == 'full':
                if expected_position is None:
                    self._remember_center(panel, result)
                return result
        self.warm_parameters, self.warm_margin = None, None
        self.warm_center, self.local_steps = None, 0
        result = self._match_panel(panel, road, valid, player, review, expected_position)
        result['full_search_retry'] = True
        if local:
            result['local_search_retry'] = True
        self._remember_center(panel, result)
        return result

    def _remember_center(self, panel, result):
        """只缓存已接受的面板中心底图坐标，失败立即使局部先验失效。

        位置种子始终使用面板几何中心，而非圆环或视口种子 player，避免普通定位和已知小队
        侦察切换时混用锚点。局部尺度与分差取最近一次全图已接受结果，不改动原全局尺度网格
        缓存；全图搜索重置连续局部计数，局部命中只递增计数。
        """
        if result['status'] != 'accepted':
            self.warm_center, self.local_steps = None, 0
            return
        center = np.array(panel.shape[1::-1], float) / 2
        matrix = np.asarray(result['roi_to_map'])
        self.warm_center = cv2.perspectiveTransform(center[None, None], matrix)[0, 0]
        if result['search_scope'] != 'local':
            self.local_parameters = (result['a'], result['b'])
            self.local_margin = result['coarse_margin']
        self.local_steps = getattr(self, 'local_steps', 0) + 1 if result['search_scope'] == 'local' else 0

    def _match_panel(self, panel, road, valid, player, review=None, expected_position=None, local=False):
        """按尺度参数网格和道路 IoU 配准，局部模式只裁剪平移搜索范围。

        局部窗口围绕上一可信面板中心构造，各候选按自己的投影保留完整模板支撑域；裁剪原点
        加回粗搜位移后才细化。局部候选不足、尺度变化或贴边均不接受，由外层清除位置先验后全图找位。
        局部分差最多沿用上次全图分差，不能把窗口外的候选消失当作置信度提升。

        expected_position 只接受小队未移动时已确认的底图坐标，player 必须是当前帧实测圆环，
        不能用视口中心替代。有此约束时全扫尺度参数，仅搜索圆环投影偏差不超过 12 像素的位置；
        道路重合度和候选离散度仍须过线，不强制把圆环钉在旧坐标，也不读写无约束热启动缓存。
        """
        expected = None if expected_position is None else np.asarray(expected_position, float)
        if expected is not None and (expected.shape != (2,) or not np.isfinite(expected).all()):
            raise ValueError('小队定位约束必须是有限的二维底图坐标。')
        local_center = self.warm_center.copy() if local else None
        coarse = []
        # 候选分门槛取 0.6：粗搜网格 IoU 明显低于接受线（全分辨率细化后要求 ACCEPT_ROAD_IOU）的参数
        # 不会成为最终候选，热启动时不值得为它们做完全网格。
        warm_score_floor = .6

        def restrict(surface, origin, scale):
            """只保留小队投影处于可信坐标半径内的平移分数，其余位置设为负值以禁止命中。"""
            if expected is None:
                return
            ys, xs = np.ogrid[:surface.shape[0], :surface.shape[1]]
            center = expected * scale - origin
            surface[np.hypot(xs - center[0], ys - center[1]) > SQUAD_ANCHOR_RADIUS * scale] = -1.

        def coarse_seed(parameters):
            """独立计算一个参数组合的粗搜候选，裁剪原点只作用于本任务的分数图。"""
            a, b = parameters
            template = template_matrix(self.projection, a, b, road.shape)
            if template is None:
                return None
            matrix, size = template
            origin = np.zeros(2, int)
            target = self.target
            if local:
                center = np.array(panel.shape[1::-1], float) / 2
                projected = cv2.perspectiveTransform(center[None, None], matrix)[0, 0]
                predicted = local_center * SCALE + PAD - projected
                radius = LOCAL_SEARCH_RADIUS * SCALE
                origin = np.maximum(0, np.ceil(predicted - radius).astype(int))
                limit = np.minimum(np.asarray(target.shape[1::-1]) - size + 1,
                                   np.floor(predicted + radius).astype(int) + 1)
                if np.any(limit <= origin):
                    return None
                target = target[origin[1]:limit[1] + size[1] - 1,
                                origin[0]:limit[0] + size[0] - 1]
            surface = iou_surface(target, road, valid, matrix, size)
            if surface is None:
                return None
            p = cv2.perspectiveTransform(player[None, None], matrix)[0, 0]
            restrict(surface, p + origin - PAD, SCALE)
            _, score, _, location = cv2.minMaxLoc(surface)
            if score < 0:
                return None
            cv2.circle(surface, location, round(130 * SCALE), 0, -1)
            _, second, _, second_location = cv2.minMaxLoc(surface)
            location = np.asarray(location) + origin
            second_location = np.asarray(second_location) + origin
            return {'a': a, 'b': b, 'score': score, 'second': second,
                    'position': (np.asarray(location) + p - PAD) / SCALE,
                    'second_position': (np.asarray(second_location) + p - PAD) / SCALE,
                    'location': location, 'matrix': matrix, 'size': size}

        def sweep(parameters):
            """并行遍历有序参数网格，剔除无效候选但不改变有效候选的稳定顺序。"""
            return [item for item in search_candidates(coarse_seed, sorted(parameters)) if item is not None]

        # seed_matcher 等旁路构造的实例没有热启动字段；热启动只对持有完整画布的实例有意义。
        warm = (self.local_parameters if local else getattr(self, 'warm_parameters', None))
        if expected is not None:
            warm = None
        warm_hit = False
        if warm is not None:
            steps = sorted({b for _, b in PARAMETER_GRID})
            neighborhood = {(a, b) for a, b in PARAMETER_GRID
                            if abs(steps.index(b) - steps.index(warm[1])) <= WARM_START_RADIUS
                            and abs(a - warm[0]) <= .21 * (WARM_START_RADIUS + 1) + 1e-9}
            coarse = sweep(neighborhood)
            # 失败交给外层统一回退，避免已全扫失败后又重复同一次全参数搜索。
            warm_hit = bool(coarse) and max(item['score'] for item in coarse) >= warm_score_floor
            if not warm_hit:
                raise ValueError('局部道路搜索没有达到粗搜分数门限。' if local
                                 else '全图道路搜索没有达到尺度热启动分数门限。')
        else:
            coarse = sweep(PARAMETER_GRID)
        if not coarse:
            raise ValueError('No usable registration candidates.')
        coarse.sort(key=lambda r: r['score'], reverse=True)

        def refine_seed(seed):
            """在粗搜位移附近独立细化一个候选，输出顺序仍由粗搜排序决定。"""
            full = np.diag([1 / SCALE, 1 / SCALE, 1.]) @ seed['matrix']
            size = tuple(round(v / SCALE) for v in seed['size'])
            predicted = np.asarray(seed['location']) / SCALE - PAD / SCALE
            start = np.rint(predicted).astype(int) + self.pad - 20
            if min(start) < 0:
                return None
            patch = self.large[start[1]:start[1] + size[1] + 40, start[0]:start[0] + size[0] + 40]
            surface = iou_surface(patch, road, valid, full, size)
            if surface is None:
                return None
            player_offset = cv2.perspectiveTransform(player[None, None], full)[0, 0]
            restrict(surface, start - self.pad + player_offset, 1.)
            _, score, _, location = cv2.minMaxLoc(surface)
            if score < 0:
                return None
            offset = start + np.asarray(location) - self.pad
            matrix = np.array([[1, 0, offset[0]], [0, 1, offset[1]], [0, 0, 1.]]) @ full
            point = cv2.perspectiveTransform(player[None, None], matrix)[0, 0]
            return {'road_iou': score, 'position': point.tolist(), 'roi_to_map': matrix.tolist(),
                    'a': seed['a'], 'b': seed['b']}

        candidates = [item for item in search_candidates(refine_seed, coarse[:8]) if item is not None]
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
        # 参数邻域没有覆盖完整网格，只能沿用同参数的已接受全图分差；局部裁剪还会排除远处
        # 候选，所以只允许降低该分差，不能把窗口外的证据缺失算作置信度提升。
        same_parameters = warm_hit and warm == (result['a'], result['b'])
        reference_margin = self.local_margin if local else getattr(self, 'warm_margin', None)
        if same_parameters and reference_margin is not None:
            margin = min(margin, reference_margin) if local else reference_margin
        accepted = bool(inside and result['road_iou'] >= ACCEPT_ROAD_IOU and margin >= ACCEPT_COARSE_MARGIN
                        and spread <= ACCEPT_PARAMETER_SPREAD_PX)
        if local:
            center = np.array(panel.shape[1::-1], float) / 2
            projected = cv2.perspectiveTransform(center[None, None], np.asarray(result['roi_to_map']))[0, 0]
            displacement = float(np.max(np.abs(projected - local_center)))
            accepted = bool(accepted and same_parameters and reference_margin is not None
                            and displacement <= LOCAL_SEARCH_RADIUS - LOCAL_REFINE_GUARD)
            result['local_displacement_px'] = displacement
        result.update(status='accepted' if accepted else 'needs_review',
                      search_scope='local' if local else ('warm' if warm_hit else 'full'), coarse_margin=float(margin),
                      parameter_spread_px=float(spread), roi=list(ROI), player_center=player.tolist(),
                      normalized_size=[1920, 1080], image_sha256=self.digest, match_version=MATCH_VERSION)
        if expected is not None:
            result.update(expected_position=expected.tolist(), anchor_error=float(np.linalg.norm(point - expected)))
        # 只有全网格 accepted 才更新热启动：needs_review 的定位不可信，把它的参数喂给下一帧
        # 会把漂移延续下去；accepted 复用同一参数直接命中，后续帧全部走小网格并复用 margin。
        if expected is None and accepted and not warm_hit and hasattr(self, 'warm_parameters'):
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


def overlap(first, second, valid, shift):
    """在两帧共同有效域内计算平移后的道路交并比和并集面积。"""
    matrix = np.float32([[1, 0, -shift[0]], [0, 1, -shift[1]]])
    size = first.shape[::-1]
    moved = cv2.warpAffine(second, matrix, size, flags=cv2.INTER_NEAREST)
    common = (valid > 0) & (cv2.warpAffine(valid, matrix, size, flags=cv2.INTER_NEAREST) > 0)
    a, b = first > 0, moved > 0
    union = int(((a | b) & common).sum())
    return float((a & b & common).sum() / max(1, union)), union


def register(first, second, valid, prediction=(0, 0)):
    """用预测位移、相位相关和零位移初始化 ECC，只接受道路重叠充分的平移。

    Translation after grid-plane rectification; reject texture-only matches.
    """
    stationary_iou, stationary_area = overlap(first, second, valid, (0, 0))
    if stationary_iou >= 0.995 and stationary_area >= 80:
        return {'delta': [0.0, 0.0], 'iou': stationary_iou, 'area': stationary_area}
    if min(np.count_nonzero(first), np.count_nonzero(second)) < 150:
        return None
    a = cv2.GaussianBlur(first.astype(np.float32) / 255, (7, 7), 0)
    b = cv2.GaussianBlur(second.astype(np.float32) / 255, (7, 7), 0)
    phase, _ = cv2.phaseCorrelate(a, b)
    seeds = [prediction, phase, (0, 0)]
    best = None
    for seed in seeds:
        if max(abs(float(value)) for value in seed) > max(first.shape) * 0.65:
            continue
        matrix = np.float32([[1, 0, seed[0]], [0, 1, seed[1]]])
        try:
            _, matrix = cv2.findTransformECC(
                a, b, matrix, cv2.MOTION_TRANSLATION,
                (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 100, 1e-5), valid, 5,
            )
        except cv2.error:
            continue
        delta = matrix[:, 2].astype(float)
        score, area = overlap(first, second, valid, delta)
        if area < 80 or np.linalg.norm(delta) > max(first.shape) * 0.65:
            continue
        if best is None or score > best['iou']:
            best = {'delta': delta.tolist(), 'iou': score, 'area': area}
    return best if best and best['iou'] >= 0.86 else None
