"""分层地图的局部实测标定与移动；不同章节保存各自的地图和场景证据。"""

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from . import runtime
from .edited_map import road_distance
from .local_projection import frame_plane_to_map
from .surface_motion import contains, fit_calibration, project, register_surface


def track_localization(localizer, reference, report, current, point=None):
    """短距离跟踪始终回到已通过全局定位的实拍帧，不累积逐步位移误差。"""
    from dev_tools.minimap_reconstruct import detect_markers
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
    return dict(image_sha256=localizer.digest, edits_sha256=localizer.edits_digest,
                geometry_sha256=localizer.cache_digest, chapter=localizer.metadata['chapter'], difficulty=difficulty)


def recover_localization(localizer, references, current):
    """仅用直接通过固定参考定位的实拍帧交叉恢复，不再把恢复结果作为下一跳参考。"""
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


def load_calibration(package, localizer, difficulty):
    path = Path(package) / 'movement_calibration' / difficulty
    metadata = path / 'calibration.json'
    if not metadata.exists():
        raise ValueError('这张地图尚无场景标定，请先点击“采集移动标定”。')
    data = json.loads(metadata.read_text(encoding='utf-8'))
    if data.get('method') != 'local_displacement':
        raise ValueError('请重新采集局部位移标定。')
    if data.get('binding') != binding(localizer, difficulty):
        raise ValueError('地图或道路修订已变化，请重新采集移动标定。')
    raw = (path / 'reference.png').read_bytes()
    if (hashlib.sha256(raw).hexdigest() != data['reference_sha256']
            or hashlib.sha256((path / 'surface.png').read_bytes()).hexdigest() != data['surface_sha256']):
        raise ValueError('场景标定参考图或道路证据发生变化。')
    return data, cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def run_movement(session):
    """显式采集或按需自动标定，验证保存成功后才进入原移动闭环。"""
    from .manual_move import navigate
    request = session.request
    if request.get('action') == 'calibrate':
        return calibrate(session)
    try:
        session.calibration = load_calibration(request['package'], session.localizer, request['difficulty'])
        session.emit(calibration_state='reused', message='已复用保存的标定，正在准备移动…')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if not request.get('auto_calibrate', False):
            raise
        session.emit(state='calibrating', calibration_state='collecting',
                     message=f'需要自动标定：{exc}。正在检查当前位置并采集…')
        result = calibrate(session)
        session.check()
        session.calibration = load_calibration(request['package'], session.localizer, request['difficulty'])
        session.emit(state='planning', calibration_state='ready', calibration_clicks=result['movement_clicks'],
                     movement_clicks=0, message='自动标定已保存，继续移动到所选目标…')
    return navigate(session, request['target'], session.emit, arrival_radius=20, required_near=2,
                    purpose=request.get('purpose', 'position'))


def surface_mask(localizer, observation):
    frame, label = observation['reference_frame'], observation['local_surface']
    ref = localizer.references[frame]
    plane = localizer.metadata['frame_support'][frame]['local_planes'][label]
    matrix = frame_plane_to_map(localizer.metadata, frame, plane)
    mask = cv2.warpPerspective((ref['labels'] == label).astype(np.uint8), matrix,
                               tuple(localizer.metadata['size']), flags=cv2.INTER_NEAREST)
    mask &= localizer.road
    count, labels = cv2.connectedComponents(mask)
    x, y = np.rint(observation['position']).astype(int)
    if not 0 <= x < mask.shape[1] or not 0 <= y < mask.shape[0] or not labels[y, x]:
        raise ValueError('小队位置缺少本层道路证据。')
    return (labels == labels[y, x]).astype(np.uint8)


def checked_segment(road, start, end):
    points = np.rint(np.linspace(start, end, max(2, int(np.linalg.norm(np.asarray(end) - start)) + 1))).astype(int)
    if (np.any(points < 0) or np.any(points >= road.shape[::-1])
            or not np.all(road[points[:, 1], points[:, 0]])):
        raise ValueError('落点之间没有同层连续道路，不跨越空洞、擦除区或高低层。')


def calibrate(session):
    """采集九个实际停靠点，六点拟合、三点验证；失败保留日志而不发布标定。"""
    from . import goto
    from .movement_feedback import resolve_anchor
    observation = session.observe()
    goto.map_close(session.win)
    reference = goto.capture_client(session.win)
    session.identity(reference)
    session.preview(reference)
    origin = np.asarray(observation['position'], float)
    supported = surface_mask(session.localizer, observation)
    clearance = cv2.distanceTransform(supported, cv2.DIST_L2, 5)
    session.preview(reference)
    runtime.write_image(session.folder / 'calibration_reference.png', reference)
    samples, evidence, locations = [], [], [origin]
    # 验证点位于外围训练点形成的区域内部，不用外推冒充验证。
    offsets = [(40, 25), (-40, -25), (40, -25), (-40, 25), (0, 25), (0, -25),
               (-15, -10), (15, -10), (0, 10)]
    current = reference
    for index, offset in enumerate(offsets):
        session.check()
        x, y = np.rint(observation['position']).astype(int)
        if not (0 <= y < clearance.shape[0] and 0 <= x < clearance.shape[1]) or clearance[y, x] < 40:
            raise ValueError('标定需要同一区域内四周至少 40 地图像素的开阔道路；当前位置靠近边界，未追加试点。')
        anchor = resolve_anchor(session, current, observation, allow_scene=False)
        before = np.asarray(observation['position'], float)
        click = anchor + offset
        if not (250 <= click[0] <= 1500 and 180 <= click[1] <= 880):
            raise ValueError('标定落点超出有效场景区域。')
        session.emit(state='calibrating', message=f'正在采集实际停靠点 {index + 1}/9…',
                     movement_clicks=index, click=click.tolist())
        session.move(click)
        session.emit(movement_clicks=index + 1)
        session.wait_stopped()
        observation = session.observe()
        if road_distance(supported, observation['position']) > 2:
            raise ValueError('标定时小队离开原道路表面，停止采集。')
        goto.map_close(session.win)
        current = goto.capture_client(session.win)
        session.identity(current)
        session.preview(current)
        after = np.asarray(observation['position'], float)
        if np.linalg.norm(after - before) < 2:
            raise ValueError('点击后没有可测的小队位移，停止标定。')
        samples.append([(after - before).tolist(), list(offset)])
        locations.append(after)
        evidence.append(dict(before=before.tolist(), after=after.tolist(), click=click.tolist(),
                             before_anchor=anchor.tolist()))
        (session.folder / 'calibration_samples.json').write_text(json.dumps(samples, indent=2), encoding='utf-8')
        (session.folder / 'calibration_observations.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    return publish_calibration(session, reference, origin, supported, samples, evidence, locations)


def publish_calibration(session, reference, origin, supported, samples, evidence, locations):
    """按地图到点容差验证实测位移；场景像素只作诊断，避免混用两种坐标单位。"""
    calibration = fit_calibration(samples[:6], samples[6:], map_error_limits=(8, 12))
    if np.linalg.norm(np.asarray(calibration['matrix'])[:2, 2]) > 8:
        raise ValueError('标定出现过大的固定偏移，可能受到障碍或定位偏差影响。')
    reference_hash = hashlib.sha256((session.folder / 'calibration_reference.png').read_bytes()).hexdigest()
    calibration.update(binding=binding(session.localizer, session.request['difficulty']),
                       reference_sha256=reference_hash,
                       method='local_displacement', origin=origin.tolist(), samples=samples,
                       origin_anchor=evidence[0]['before_anchor'],
                       radius=max(float(np.linalg.norm(p - origin)) for p in locations) + 12)
    destination = Path(session.request['package']) / 'movement_calibration' / session.request['difficulty']
    destination.mkdir(parents=True, exist_ok=True)
    # 先在独立任务目录验证完，再发布；旧标定留在带时间戳的备份中。
    import time
    import shutil
    if (destination / 'calibration.json').exists():
        backup = destination / f'backup_{time.time_ns()}'
        backup.mkdir()
        for name in ['reference.png', 'surface.png', 'calibration.json']:
            if (destination / name).exists():
                shutil.copyfile(destination / name, backup / name)
    runtime.write_image(destination / 'reference.png', reference)
    runtime.write_image(destination / 'surface.png', supported * 255)
    calibration['surface_sha256'] = hashlib.sha256((destination / 'surface.png').read_bytes()).hexdigest()
    pending = destination / 'calibration.pending.json'
    pending.write_text(json.dumps(calibration, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(destination / 'calibration.json')
    return dict(state='calibrated', message='局部场景标定通过，可以测试该道路已标定区域内的目标。',
                movement_clicks=len(samples), calibration_path=str(destination),
                validation=calibration['validation_map_max_px'])


class ParallaxSessionMixin:
    def observe(self):
        from . import goto
        self.check()
        if self.win is None:
            self.win = runtime.Window()
            self.win.focus()
        self.identity(goto.capture_client(self.win))
        goto.map_open(self.win)
        from dev_tools.minimap_reconstruct import detect_markers
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
                report = recover_localization(self.localizer, getattr(self, 'live_references', []), image)
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
        from . import goto
        (self.folder / f'localization_{self.index:03}.json').write_text(json.dumps(report), encoding='utf-8')
        if report['status'] != 'accepted':
            raise ValueError(f"小队分层定位未通过：{report.get('reason', '证据不足')}")
        if np.mean(goto.mr.terrain(image) != goto.mr.terrain(self.win.capture())) > .03:
            raise ValueError('定位期间地图发生变化。')
        report['enemy_markers'] = goto.normal_enemy_markers(image, np.asarray(report['roi_to_map']))
        return report

    def plan(self, observation, target):
        from . import goto
        from .movement_feedback import resolve_anchor
        data, _ = self.calibration
        position = np.asarray(observation['position'], float)
        if np.linalg.norm(np.asarray(target) - data['origin']) > data['radius']:
            raise ValueError('目标或小队超出实测标定范围；请在已标定的局部道路内选点。')
        return_margin = max(np.linalg.norm(p) for p in data['support'])
        if np.linalg.norm(position - data['origin']) > data['radius'] + return_margin:
            raise ValueError('小队离标定区域太远，请先返回已标定的同层道路附近。')
        path = Path(self.request['package']) / 'movement_calibration' / self.request['difficulty'] / 'surface.png'
        if hashlib.sha256(path.read_bytes()).hexdigest() != data['surface_sha256']:
            raise ValueError('标定道路掩码发生变化。')
        mask = (cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) > 0).astype(np.uint8) & self.localizer.road
        checked_segment(mask, observation['position'], target)
        goto.map_close(self.win)
        field = goto.capture_client(self.win)
        self.identity(field)
        self.check_collectible(field)
        self.preview(field)
        delta = np.asarray(target) - position
        for _ in range(8):
            if contains(data['support'], delta):
                break
            delta *= .75
        else:
            raise ValueError('移动方向不在实测位移范围内。')
        if not hasattr(self, 'anchor_reference') and data.get('origin_anchor'):
            self.anchor_reference = (self.calibration[1], np.asarray(data['origin_anchor']), np.asarray(data['origin']))
        click = resolve_anchor(self, field, observation) + project(data['matrix'], delta)
        if not (250 <= click[0] <= 1500 and 180 <= click[1] <= 880):
            raise ValueError('目标超出有效场景点击区域。')
        return click
