"""分层地图的临时两点标定与同层移动；标定只在本次会话内存中使用，不写入地图包。"""

import hashlib
import json
import types
from pathlib import Path

import cv2
import numpy as np

from . import runtime
from .edited_map import road_distance
from .local_projection import frame_plane_to_map
from .probe import Localizer as FlatLocalizer, NoConnectedRoad, jacobian
from .surface_motion import contains, project, register_surface

# 两次正交短停靠：方向固定，步长按开阔程度在 80/60/40 客户区像素中取最长，停靠噪声约 5 地图像素，
# 步长越长相对误差越小；先验权重与重标次数上限按 40 章实测样本的留出模拟选定。
CALIBRATION_DIRECTIONS = ((1., 0.), (0., 1.))
CALIBRATION_LENGTHS = (80., 60., 40.)
PRIOR_WEIGHT = .3
RECALIBRATION_LIMIT = 2


def track_localization(localizer, reference, report, current, point=None):
    """短距离跟踪始终回到已通过全局定位的实拍帧，不累积逐步位移误差。

    reference/report 必须属于已验证的固定参考，current 是新 ROI；未传 point 时要求唯一小队圆环。
    比较多个局部道路范围的留出验证和坐标一致性，返回保留来源身份的新报告；支持不足或分歧超过 3px 时拒绝。
    """
    from .perception import detect_markers
    if point is None:
        players, _ = detect_markers(current, np.eye(3))
        if len(players) != 1:
            raise ValueError('短距离跟踪无法唯一识别小队圆环。')
        point = players[0]
    point = np.asarray(point)
    center = np.asarray(report['player_roi'])
    candidates = []
    for radius in range(70, 141, 10):
        polygon = np.clip([center + delta for delta in
                           [(-radius, -radius), (radius, -radius), (radius, radius), (-radius, radius)]],
                          [15, 15], np.asarray(reference.shape[1::-1]) - 16)
        try:
            fit = register_surface(reference, current, polygon, minimap=True)
        except ValueError:
            continue
        source = project(np.linalg.inv(fit['matrix']), point)
        if not contains(fit['support'], source):
            continue
        matrix = np.asarray(report['roi_to_map']) @ np.linalg.inv(fit['matrix'])
        position = project(matrix, point)
        if not localizer.allows_position(position):
            continue
        candidates.append(dict(position=position.tolist(), roi_to_map=matrix.tolist(), registration=fit, radius=radius))
    if not candidates or (len(candidates) == 1 and (candidates[0]['registration']['points'] < 12
                                                   or candidates[0]['registration']['validation_points'] < 4)):
        raise ValueError('短距离跟踪缺少足够的独立留出角点。')
    spread = max(float(np.linalg.norm(np.asarray(p['position']) - candidates[0]['position'])) for p in candidates)
    if spread > 3:
        raise ValueError('两组局部道路跟踪结果不一致。')
    return {**report, **candidates[0], 'player_roi': point.tolist(), 'player_center': point.tolist(),
            'status': 'accepted', 'position_kind': 'squad', 'tracking_spread_px': spread,
            'reference_depth': report.get('reference_depth', 0) + 1,
            'registration_method': 'fixed_live_reference', 'candidates': candidates}


def binding(localizer, difficulty):
    """返回底图、道路修订、几何、章节和难度组成的缓存身份字典。

    同一字典用于保存和加载参考帧，防止把不同版本道路上的证据混用。
    """
    return dict(image_sha256=localizer.digest, edits_sha256=localizer.edits_digest,
                geometry_sha256=localizer.cache_digest, chapter=localizer.metadata['chapter'], difficulty=difficulty)


def recover_localization(localizer, references, current):
    """用直接通过固定参考定位的实拍帧交叉恢复，禁止恢复结果作为下一跳参考。

    最多使用最近三组直接参考跟踪证据，至少两组恢复成功且位置差不超过 6px。
    返回带交叉恢复标记的报告；恢复报告不应成为后续参考，以限制多跳配准漂移。
    """
    candidates = []
    for image, report in references[-3:]:
        try:
            candidates.append(track_localization(localizer, image, report, current))
        except ValueError:
            continue
    if len(candidates) < 2:
        raise ValueError('遮挡恢复缺少两组独立实拍道路证据。')
    points = np.asarray([entry['position'] for entry in candidates])
    if np.max(np.linalg.norm(points[:, None] - points[None, :], axis=2)) > 6:
        raise ValueError('实拍道路恢复的位置不一致。')
    result = candidates[0]
    result.update(registration_method='cross_checked_live_references', recovery_positions=points.tolist())
    return result


def save_live_references(cache, localizer, difficulty, references):
    """只保留 accepted、参考深度不超过一层且非交叉恢复的最近三组图像与报告。

    图片写入后将哈希和绑定信息通过临时 JSON 原子发布；不合格参考不会写入复用记录。
    """
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    direct = [(image, report) for image, report in references
              if report.get('status') == 'accepted' and report.get('reference_depth', 0) <= 1
              and report.get('registration_method') != 'cross_checked_live_references']
    for index, (image, report) in enumerate(direct[-3:]):
        name = f'live_{index}'
        runtime.write_image(cache / f'{name}.png', image)
        record = dict(binding=binding(localizer, difficulty), report=report,
                      sha256=hashlib.sha256((cache / f'{name}.png').read_bytes()).hexdigest())
        pending = cache / f'{name}.pending.json'
        pending.write_text(json.dumps(record), encoding='utf-8')
        pending.replace(cache / f'{name}.json')


def load_live_references(cache, localizer, difficulty):
    """逐个检查最多三个缓存槽的地图绑定、图片哈希、定位状态和参考深度。

    缺失、损坏或不合格记录被跳过，返回解码成功的图像／报告列表，空列表由恢复逻辑决定是否失败。
    """
    references = []
    for index in range(3):
        path = Path(cache) / f'live_{index}.json'
        if not path.exists():
            continue
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            raw = path.with_suffix('.png').read_bytes()
            report = record['report']
            if (record['binding'] != binding(localizer, difficulty) or report.get('status') != 'accepted'
                    or report.get('reference_depth', 0) > 1
                    or report.get('registration_method') == 'cross_checked_live_references'
                    or hashlib.sha256(raw).hexdigest() != record['sha256']):
                continue
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        except (OSError, ValueError, KeyError, TypeError, cv2.error):
            continue
        if image is not None:
            references.append((image, report))
    return references


def surface_identity(localizer, observation):
    """返回观测所在局部平面的章节级表面编号；包未聚合表面时为 None。"""
    plane = localizer.metadata['frame_support'][observation['reference_frame']]['local_planes'][
        observation['local_surface']]
    return plane.get('surface_id') if plane else None


def surface_union(localizer, identity):
    """同一表面编号下所有局部平面投影到地图的并集，按定位器缓存一次。"""
    cache = getattr(localizer, '_surface_unions', None)
    if cache is None:
        cache = localizer._surface_unions = {}
    if identity not in cache:
        size = tuple(localizer.metadata['size'])
        union = np.zeros(size[::-1], np.uint8)
        for frame, entry in enumerate(localizer.metadata['frame_support']):
            for label, plane in enumerate(entry['local_planes']):
                if plane is None or plane.get('surface_id') != identity:
                    continue
                matrix = frame_plane_to_map(localizer.metadata, frame, plane)
                union |= cv2.warpPerspective((localizer.references[frame]['labels'] == label).astype(np.uint8),
                                             matrix, size, flags=cv2.INTER_NEAREST)
        cache[identity] = union
    return cache[identity]


def surface_mask(localizer, observation):
    """小队所在表面的道路掩码：有表面编号时取同编号平面的并集，否则只用观测帧的局部平面。

    返回包含小队的连通区域；小队不在有效表面内时抛错，不能用相邻层补足道路。
    """
    frame, label = observation['reference_frame'], observation['local_surface']
    identity = surface_identity(localizer, observation)
    if identity is None:
        plane = localizer.metadata['frame_support'][frame]['local_planes'][label]
        mask = cv2.warpPerspective((localizer.references[frame]['labels'] == label).astype(np.uint8),
                                   frame_plane_to_map(localizer.metadata, frame, plane),
                                   tuple(localizer.metadata['size']), flags=cv2.INTER_NEAREST)
    else:
        mask = surface_union(localizer, identity).copy()
    mask &= localizer.road
    count, labels = cv2.connectedComponents(mask)
    x, y = np.rint(observation['position']).astype(int)
    if not 0 <= x < mask.shape[1] or not 0 <= y < mask.shape[0] or not labels[y, x]:
        raise ValueError('小队位置缺少本层道路证据。')
    return (labels == labels[y, x]).astype(np.uint8)


def surface_route(mask, start, target):
    """在同层道路掩码上复用平面原型的 A*；二维连通不证明跨层可通行。"""
    shim = types.SimpleNamespace(road=mask.astype(np.float32), route_labels=None)
    return FlatLocalizer.route(shim, start, target)


def checked_segment(road, start, end):
    """沿 start 到 end 按约一地图像素间距采样并检查二值道路掩码。

    无返回值；越界或任何采样点无道路都抛出 ValueError，用于阻止直线跨空洞和擦除区。
    """
    points = np.rint(np.linspace(start, end, max(2, int(np.linalg.norm(np.asarray(end) - start)) + 1))).astype(int)
    if (np.any(points < 0) or np.any(points >= road.shape[::-1])
            or not np.all(road[points[:, 1], points[:, 0]])):
        raise ValueError('落点之间没有同层连续道路，不跨越空洞、擦除区或高低层。')


def flat_prior(observation):
    """把 38 章共用的场景矩阵换到当前观测的地图坐标：两侧都在小队处做局部线性化。

    先验是仓库内的静态资产，不是学习值；它只用来约束两点拟合，不单独用于点击。
    """
    asset = json.loads((Path(__file__).parent / 'assets/calibration.json').read_text(encoding='utf-8'))
    roi = np.asarray(observation['player_roi'], float)
    flat = jacobian(np.asarray(asset['projection'], float), roi)
    layered = jacobian(np.asarray(observation['roi_to_map'], float), roi)
    return np.asarray(asset['field_inverse'], float) @ flat @ np.linalg.inv(layered)


def fit_quick(samples, prior, weight=PRIOR_WEIGHT):
    """向先验收缩的两点岭回归；样本是 (地图位移, 客户区偏移) 对，返回 3×3 矩阵与残差摘要。

    岭回归让近共线或带噪的两点不会把矩阵放大到先验之外，40 章样本模拟下留出残差中位约 7px。
    """
    samples = np.asarray(samples, float)
    if samples.ndim != 3 or samples.shape[1:] != (2, 2) or len(samples) < 2:
        raise ValueError('临时标定至少需要两个样本。')
    displacement, offset = samples[:, 0], samples[:, 1]
    if np.linalg.matrix_rank(displacement, tol=1.) < 2:
        raise ValueError('两次停靠的地图位移方向过于接近，无法标定两个轴。')
    ridge = weight * float(np.mean(np.sum(displacement ** 2, axis=1)))
    linear = (offset.T @ displacement + ridge * prior) @ np.linalg.inv(displacement.T @ displacement
                                                                      + ridge * np.eye(2))
    matrix = np.eye(3)
    matrix[:2, :2] = linear
    residual = np.linalg.norm(displacement @ linear.T - offset, axis=1)
    prior_residual = np.linalg.norm(displacement @ prior.T - offset, axis=1)
    return matrix, dict(samples=int(len(samples)), ridge_lambda=ridge,
                        fit_residual_max_px=float(residual.max()), prior_residual_max_px=float(prior_residual.max()))


def calibration_offset(prior, supported, position, direction, lengths=CALIBRATION_LENGTHS):
    """按先验预测落点，在同层掩码上沿给定方向选最长且全程净空足够的客户区偏移。

    先验只用来预测大致落点，误差两成以内不影响净空判断；没有一档可行时拒绝标定而不是缩到更短。
    """
    clearance = cv2.distanceTransform(supported, cv2.DIST_L2, 5)
    inverse = np.linalg.inv(prior)
    for length in lengths:
        offset = np.asarray(direction, float) * length
        landing = position + inverse @ offset
        count = max(2, int(np.linalg.norm(landing - position)) + 1)
        line = np.rint(np.linspace(position, landing, count)).astype(int)
        if np.any(line < 0) or np.any(line >= np.asarray(supported.shape[::-1])):
            continue
        if np.all(clearance[line[:, 1], line[:, 0]] >= 12):
            return offset
    raise ValueError('临时标定需要小队沿两个方向各有至少 40 客户区像素的开阔同层道路；请先把小队移到开阔处。')


def quick_calibrate(session, directions=CALIBRATION_DIRECTIONS):
    """两次正交短停靠拟合本次会话的临时点击矩阵，返回标定结束时的新观测；不写任何地图包文件。

    每次停靠先直接采样箭头，再执行客户区短偏移，停稳后用真实地图位移构造样本；样本与证据写在任务目录。
    """
    from . import goto
    from .movement_feedback import resolve_anchor
    observation = session.observe()
    goto.map_close(session.win)
    reference = goto.capture_client(session.win)
    session.identity(reference)
    session.preview(reference)
    supported = surface_mask(session.localizer, observation)
    clearance = cv2.distanceTransform(supported, cv2.DIST_L2, 5)
    runtime.write_image(session.folder / 'calibration_reference.png', reference)
    prior = flat_prior(observation)
    origin = np.asarray(observation['position'], float)
    origin_anchor = None
    samples, evidence, current = [], [], reference
    for index, direction in enumerate(directions):
        session.check()
        x, y = np.rint(observation['position']).astype(int)
        if not (0 <= y < clearance.shape[0] and 0 <= x < clearance.shape[1]) or clearance[y, x] < 40:
            raise ValueError('临时标定需要同一表面四周至少 40 地图像素的开阔道路；请先把小队移到开阔处。')
        offset = calibration_offset(prior, supported, np.asarray(observation['position'], float), direction)
        anchor = resolve_anchor(session, current, observation, allow_scene=False)
        if origin_anchor is None:
            origin_anchor = anchor
        before = np.asarray(observation['position'], float)
        click = anchor + offset
        if not (250 <= click[0] <= 1500 and 180 <= click[1] <= 880):
            raise ValueError('标定落点超出有效场景区域。')
        session.emit(state='calibrating', message=f'正在做两点临时标定 {index + 1}/{len(directions)}…',
                     calibration_clicks=index, click=click.tolist())
        session.move(click)
        session.emit(calibration_clicks=index + 1)
        session.wait_stopped()
        observation = session.observe()
        if road_distance(supported, observation['position']) > 2:
            raise ValueError('标定时小队离开原表面道路，停止采集。')
        goto.map_close(session.win)
        current = goto.capture_client(session.win)
        session.identity(current)
        session.preview(current)
        after = np.asarray(observation['position'], float)
        if np.linalg.norm(after - before) < 2:
            raise ValueError('点击后没有可测的小队位移，停止标定。')
        samples.append([(after - before).tolist(), offset.tolist()])
        evidence.append(dict(before=before.tolist(), after=after.tolist(), click=click.tolist(),
                             before_anchor=anchor.tolist()))
    matrix, report = fit_quick(samples, prior)
    data = dict(method='temporary_two_point', matrix=matrix.tolist(), prior=prior.tolist(), samples=samples,
                origin=origin.tolist(), origin_anchor=origin_anchor.tolist(),
                surface_id=surface_identity(session.localizer, observation), **report)
    (session.folder / 'calibration_samples.json').write_text(json.dumps(dict(data, evidence=evidence), indent=2),
                                                              encoding='utf-8')
    session.calibration = (data, reference)
    session.surface = supported
    session.last_plan = None
    session.emit(calibration_state='ready', calibration=dict(samples=report['samples'],
                                                              fit_residual_max_px=report['fit_residual_max_px'],
                                                              prior_residual_max_px=report['prior_residual_max_px']),
                 message='两点临时标定完成，继续移动…')
    return observation


def run_movement(session):
    """移动任务不读取任何保存的标定；临时标定在首次规划或显式请求时生成，任务结束即丢弃。"""
    from .manual_move import navigate
    request = session.request
    session.calibration = None
    if request.get('action') == 'calibrate':
        quick_calibrate(session)
        data = session.calibration[0]
        return dict(state='calibrated', message='两点临时标定完成，仅在本次会话内有效，不写入地图包。',
                    movement_clicks=len(data['samples']),
                    calibration=dict(samples=data['samples'], fit_residual_max_px=data['fit_residual_max_px'],
                                     prior_residual_max_px=data['prior_residual_max_px'],
                                     surface_id=data['surface_id']))
    return navigate(session, request['target'], session.emit, arrival_radius=20, required_near=2,
                    purpose=request.get('purpose', 'position'))


class ParallaxSessionMixin:
    def observe(self):
        """优先用固定实拍参考跟踪，再尝试独立实拍交叉恢复及原始帧全局定位。

        唯一小队标记最多重试六次；缓存必须通过绑定与图片哈希校验，最终结果统一经 finish_observation 检查。
        """
        from . import goto
        self.check()
        if self.win is None:
            self.win = runtime.Window()
            self.win.focus()
        field = goto.capture_client(self.win)
        self.identity(field)
        self.check_collectible(field)
        goto.map_open(self.win, reset=True)
        from .perception import detect_markers
        for attempt in range(6):
            image = self.win.capture()
            players, _ = detect_markers(image, np.eye(3))
            if len(players) == 1:
                break
            self.pause(.3)
        else:
            raise ValueError('小地图小队标记持续不可见，停止追加移动。')
        self.index += 1
        runtime.write_image(self.folder / f'roi_{self.index:03}.png', image)
        self.emit(state='locating', message='正在定位小队并核对修订道路…')
        cache = Path(self.request['package']) / '.movement_reference' / self.request['difficulty']
        if hasattr(self, 'fixed_reference'):
            reference, base = self.fixed_reference
            try:
                report = track_localization(self.localizer, reference, base, image)
                self.live_references = [*getattr(self, 'live_references', []), (image.copy(), report)][-3:]
                save_live_references(cache, self.localizer, self.request['difficulty'], self.live_references)
            except ValueError:
                try:
                    report = recover_localization(self.localizer, getattr(self, 'live_references', []), image)
                except ValueError:
                    report = None
            if report is not None:
                return self.finish_observation(image, report)
        report = self.localizer.locate_squad(image, self.folder / f'localization_{self.index:03}.jpg')
        (self.folder / f'global_localization_{self.index:03}.json').write_text(json.dumps(report), encoding='utf-8')
        if report['status'] == 'accepted':
            self.fixed_reference = (image.copy(), report)
            cache.mkdir(parents=True, exist_ok=True)
            runtime.write_image(cache / 'roi.png', image)
            record = dict(binding=binding(self.localizer, self.request['difficulty']), report=report,
                          sha256=hashlib.sha256((cache / 'roi.png').read_bytes()).hexdigest())
            (cache / 'reference.json').write_text(json.dumps(record), encoding='utf-8')
        elif (cache / 'reference.json').exists():
            record = json.loads((cache / 'reference.json').read_text(encoding='utf-8'))
            raw = (cache / 'roi.png').read_bytes()
            if (record['binding'] == binding(self.localizer, self.request['difficulty'])
                    and hashlib.sha256(raw).hexdigest() == record['sha256']):
                reference = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                self.fixed_reference = (reference, record['report'])
                self.live_references = load_live_references(cache, self.localizer, self.request['difficulty'])
                try:
                    report = track_localization(self.localizer, reference, record['report'],
                                                image, report['player_roi'])
                except ValueError:
                    report = recover_localization(self.localizer, self.live_references, image)
        if report['status'] == 'accepted':
            if report.get('reference_depth', 0) <= 1:
                self.live_references = [*getattr(self, 'live_references', []), (image.copy(), report)][-3:]
                save_live_references(cache, self.localizer, self.request['difficulty'], self.live_references)
        return self.finish_observation(image, report)

    def finish_observation(self, image, report):
        """保存定位 JSON 后确认 status=accepted，并比较定位期间的新旧道路掩码。

        变化超过 3% 时抛错；否则附加普通敌人坐标并返回报告，避免把陈旧位置交给规划器。
        """
        from . import goto
        (self.folder / f'localization_{self.index:03}.json').write_text(json.dumps(report), encoding='utf-8')
        if report['status'] != 'accepted':
            raise ValueError(f"小队分层定位未通过：{report.get('reason', '证据不足')}")
        if np.mean(goto.mr.terrain(image) != goto.mr.terrain(self.win.capture())) > .03:
            raise ValueError('定位期间地图发生变化。')
        report['enemy_markers'] = goto.normal_enemy_markers(image, np.asarray(report['roi_to_map']))
        return report

    def _surface_changed(self, observation):
        """临时标定绑定表面编号；有编号的包里小队换到另一表面就要重标。"""
        data = self.calibration[0]
        current = surface_identity(self.localizer, observation)
        return data.get('surface_id') is not None and current is not None and current != data['surface_id']

    def _poor_progress(self, observation, target):
        """同一目标的上一次直达点击若只前进了不到一半，临时矩阵不可信，允许有限次重标。"""
        last = getattr(self, 'last_plan', None)
        if (last is None or not np.allclose(last['target'], target)
                or getattr(self, 'recalibrations', 0) >= RECALIBRATION_LIMIT):
            return False
        remaining = float(np.linalg.norm(np.asarray(target, float) - np.asarray(observation['position'], float)))
        return last['distance'] >= 40 and remaining > .5 * last['distance']

    def plan(self, observation, target):
        """同层连通检查后直接规划到完整目标；首次落点进展不足或换层时重做两点临时标定。

        不再限制标定半径或采样凸包，长距离误差由停稳重定位的到点循环吸收；跨层目标须先标注电梯。
        """
        from .camera_navigation import plan_world_move
        target = np.asarray(target, float)
        if getattr(self, 'calibration', None) is None or self._surface_changed(observation):
            self.emit(state='calibrating', message='正在做本次会话的两点临时标定…')
            observation = quick_calibrate(self)
        elif self._poor_progress(observation, target):
            self.recalibrations = getattr(self, 'recalibrations', 0) + 1
            self.emit(state='calibrating', message='首次落点进展不足，重做两点临时标定…')
            observation = quick_calibrate(self)
        position = np.asarray(observation['position'], float)
        mask = surface_mask(self.localizer, observation)
        try:
            _, path = surface_route(mask, position, target)
        except NoConnectedRoad as exc:
            raise ValueError('目标与小队不在同一表面的连通道路上；跨层请先标注电梯传送。') from exc
        self.emit(road_remaining=float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum()))
        self.last_plan = dict(target=target.tolist(), distance=float(np.linalg.norm(target - position)))
        self.surface = mask
        return plan_world_move(self, observation, target, calibration=self.calibration[0], surface=mask)
