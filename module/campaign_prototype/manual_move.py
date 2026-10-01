"""从标注原图坐标自动定位、换算并移动；供本地标注管理的独立子进程调用。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import traceback

import cv2
from filelock import FileLock, Timeout
import numpy as np

from . import runtime, settings
from .map_package import MapPackage


def sha(path):
    """绑定完整文件内容，避免新标注套用旧底图坐标。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(request, folder):
    """平面地图共享第 38 章现用标定，分层地图校验各自已有标定。"""
    source = Path(request['package'])
    if request.get('action', 'move') not in ('move', 'calibrate'):
        raise ValueError('未知移动操作。')
    if sha(source / 'map.png') != request['image_sha256'] or sha(
            source / 'annotations.json') != request['revision']:
        raise ValueError('地图或标注已变化，请重新加载后选择目标。')
    metadata = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    if metadata.get('coordinate_model') == 'local_parallax':
        from .parallax_localizer import ParallaxLocalizer
        from .parallax_movement import load_calibration
        if metadata['chapter'] != request['chapter']:
            raise ValueError('所选地图章节已变化。')
        localizer = ParallaxLocalizer(source)
        if request.get('action', 'move') != 'calibrate':
            load_calibration(source, localizer, request['difficulty'])
            request['auto_calibrate'] = False
            from .edited_map import road_distance
            if road_distance(localizer.road, request['target']) > 0:
                raise ValueError('目标不在修订后的道路上。')
        names = ['map.json', 'map.png', 'annotations.json']
        return source, {name: sha(source / name) for name in names}
    if request.get('action') == 'calibrate':
        raise ValueError('该标定入口用于分层地图。')
    if metadata.get('coordinate_model') is not None:
        raise ValueError('非平面地图移动待定；当前只支持已有有效局部分层标定的地图。')
    destination = folder / 'map'
    (destination / 'source').mkdir(parents=True)
    names = ('map.json', 'map.png', 'annotations.json', 'source/map_data.npz')
    for name in names:
        shutil.copyfile(source / name, destination / name)
    if sha(destination / 'map.png') != request['image_sha256'] or sha(
            destination / 'annotations.json') != request['revision']:
        raise ValueError('生成测试快照期间地图变化，请重新选择目标。')
    hashes = {name: sha(destination / name) for name in names}
    metadata = json.loads((destination / 'map.json').read_text(encoding='utf-8'))
    if metadata['chapter'] != request['chapter']:
        raise ValueError('所选地图章节已变化。')
    # 普通与困难共用底图；难度身份只写入本次快照，并在每次移动前核对现场。
    metadata['difficulty'] = request['difficulty']
    (destination / 'map.json').write_text(json.dumps(metadata), encoding='utf-8')
    shutil.copyfile(Path(__file__).parent / 'assets/calibration.json', destination / 'calibration.json')
    manifest = {'sha256': {name: sha(destination / name) for name in (*names, 'calibration.json')},
                'client': [1776, 999], 'roi': [644, 280, 1130, 742], 'coverage': 'observed_roads',
                'whole_camera_domain_verified': metadata['capture']['whole_camera_domain_verified']}
    (destination / 'validation.json').write_text(json.dumps(manifest), encoding='utf-8')
    package = MapPackage(destination, request['chapter'], request['difficulty'])
    target = np.asarray(request['target'], float)
    if (target.shape != (2,) or not np.isfinite(target).all() or np.any(target < 0)
            or np.any(target >= metadata['size'])):
        raise ValueError('目标超出当前地图。')
    road = (package.terrain >= .5).astype(np.uint8)
    distance = cv2.distanceTransform(1 - road, cv2.DIST_L2, 5)
    x, y = np.floor(target).astype(int)
    if not road.any() or distance[y, x] > 12:
        raise ValueError('目标不在已采集道路附近，请选择道路上的目标点。')
    return destination, hashes


def movement_plan(localizer, observation, target, anchor):
    """根据当前小地图变换计算有道路支持的短落点，禁止把图像中心当成小队位置。"""
    from .probe import goto, jacobian
    if observation.get('position_kind') != 'squad' or observation.get('player_roi') is None:
        raise ValueError('未识别到小队，无法计算移动落点。')
    position = np.asarray(observation['position'], float)
    waypoint, path = localizer.route(position, target)
    delta = waypoint - position
    if np.linalg.norm(delta) < 2:
        raise ValueError('当前道路没有可用的前进落点，停止测试。')
    delta *= min(1., 70 / np.linalg.norm(delta))
    point = np.asarray(observation['player_roi'], float)
    conversion = jacobian(np.asarray(observation['roi_to_map']), point) @ np.linalg.inv(
        jacobian(localizer.old_matrix, point))
    click = goto.movement_click(np.asarray(anchor), np.linalg.solve(conversion, delta))
    return click, float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())


def navigate(session, target, emit, max_moves=24, arrival_radius=12, required_near=3, purpose='position'):
    """有限步执行并重新定位，连续三次近点观测才算到达，无进展或预算耗尽明确失败。"""
    target = np.asarray(target, float)
    clicks, near, stagnant = 0, 0, 0
    previous = None
    probes = None
    probe_index = 0
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        session.check()
        emit(state='locating', message='正在定位小队…', movement_clicks=clicks)
        observation = session.observe()
        position = np.asarray(observation['position'], float)
        if (observation.get('position_kind') != 'squad' or position.shape != (2,)
                or not np.isfinite(position).all()):
            raise RuntimeError('小队定位无效，停止移动。')
        approach = target
        if purpose == 'enemy':
            markers = [np.asarray(p) for p in observation.get('enemy_markers', [])
                       if np.linalg.norm(np.asarray(p) - target) <= 30]
            if len(markers) == 1:
                approach = markers[0]
                emit(target_marker=approach.tolist())
        distance = float(np.linalg.norm(approach - position))
        emit(position=position.tolist(), distance=distance, iou=observation['iou'])
        if distance <= arrival_radius and purpose == 'position':
            near += 1
            if near >= required_near:
                session.finish_view()
                return {'state': 'arrived', 'message': f'已到达目标附近（连续 {required_near} 次在 {arrival_radius} 地图像素内）。',
                        'movement_clicks': clicks, 'position': position.tolist(), 'distance': distance}
            emit(state='verifying', message=f'正在复核到达位置（{near}/{required_near}）…')
            session.pause(1)
            continue
        near = 0
        if previous is not None and probes is None:
            stagnant = stagnant + 1 if np.linalg.norm(position - previous) < 3 else 0
            if stagnant >= 2:
                raise RuntimeError('连续移动后小队没有明显位移，停止测试。')
        if clicks >= max_moves:
            raise RuntimeError(f'已达到 {max_moves} 次移动上限，尚未到达目标。')
        emit(state='planning', message='正在自动换算道路落点…')
        if purpose != 'position' and (distance <= arrival_radius or probes is not None):
            from .movement_feedback import probe_targets
            if probes is None:
                probes = probe_targets(approach)
            click = None
            while probe_index < len(probes):
                candidate = probes[probe_index]
                probe_index += 1
                try:
                    click = session.plan(observation, candidate)
                    break
                except ValueError as exc:
                    emit(message=f'附近落点不可用：{exc}')
            if click is None:
                session.finish_view()
                return dict(state='needs_review', message='已到目标附近，有限范围尝试后仍未确认触发，请检查现场。',
                            movement_clicks=clicks, position=position.tolist(), distance=distance)
            emit(state='probing', message=f'正在尝试目标附近落点（{probe_index}/{len(probes)}）…')
        else:
            click = session.plan(observation, approach)
        session.check()
        session.move(click)
        clicks += 1
        previous = position
        emit(state='moving', message='小队正在移动，等待停稳后重新定位…',
             movement_clicks=clicks, click=click.tolist())
        session.wait_stopped()
    raise RuntimeError('移动测试超过十分钟，停止测试。')


class GameSession:
    def __init__(self, request, hashes, folder, emit):
        """先初始化本地定位与 OCR，随后取得窗口；子进程独占鼠标生命周期。"""
        from paddleocr import TextRecognition
        from .adaptive import AdaptiveLocalizer
        self.model = TextRecognition(model_name='PP-OCRv5_mobile_rec',
                                     model_dir=str(settings.ROOT / 'bin/paddleocr/PP-OCRv5_mobile_rec_infer'),
                                     device='cpu', cpu_threads=2)
        metadata = json.loads((Path(request['package']) / 'map.json').read_text(encoding='utf-8'))
        if metadata.get('coordinate_model') == 'local_parallax':
            from .parallax_localizer import ParallaxLocalizer
            self.localizer = ParallaxLocalizer(request['package'])
        else:
            self.localizer = AdaptiveLocalizer()
        self.request, self.hashes, self.folder, self.emit = request, hashes, folder, emit
        self.index = 0
        self.win = None

    def check(self):
        """同时尊重任务停止文件与原型全局停止文件，源地图变化也禁止继续输入。"""
        runtime.check_stop()
        if (settings.ROOT / 'log/campaign_prototype/STOP').exists():
            raise KeyboardInterrupt('原型全局 STOP 已生效。')
        if self.win is not None:
            self.win.check()
        for name, expected in self.hashes.items():
            if sha(Path(self.request['package']) / name) != expected:
                raise RuntimeError('地图或标注在测试期间变化，停止移动。')

    def identity(self, field):
        """核对当前章节、难度和战斗弹窗，不自动切章或进入战斗。"""
        from dev_tools.minimap_chapters import chapter_number
        from . import goto
        if goto.battle_popup_score(field) > .8:
            from .movement_feedback import TargetTriggered
            if self.request.get('purpose') == 'enemy':
                self.preview(field)
                raise TargetTriggered('已接触敌人，战斗准备界面已出现。', {'kind': 'battle_popup'})
            raise RuntimeError('出现战斗弹窗，移动测试已停止。')
        if chapter_number(field, self.model) != self.request['chapter']:
            raise RuntimeError(f"游戏章节与所选第 {self.request['chapter']} 章不符，或章节号无法识别。")
        crop = cv2.resize(field[898:912, 1613:1670], None, fx=4, fy=4)
        text = next(iter(self.model.predict(crop)))
        if (text['rec_text'].strip().upper() != self.request['difficulty'].upper()
                or text['rec_score'] < .9):
            raise RuntimeError('游戏普通／困难难度与所选目标不符，或难度无法识别。')

    def check_collectible(self, field):
        if self.request.get('purpose') != 'collectible':
            return
        from .movement_feedback import collectible_counter, TargetTriggered
        value = collectible_counter(field, self.model)
        if value is None:
            self.counter_confirmation = 0
            return
        if not hasattr(self, 'counter_baseline'):
            self.counter_baseline = value
        base = self.counter_baseline
        if value[1] == base[1] and value[0] > base[0]:
            self.counter_confirmation = getattr(self, 'counter_confirmation', 0) + 1
            if self.counter_confirmation >= 2:
                self.preview(field)
                raise TargetTriggered('收集品计数增加，已确认拾取。', {'kind': 'collectible_counter',
                                                                   'before': base, 'after': value})
        else:
            self.counter_confirmation = 0

    def preview(self, field):
        """原子更新压缩截图供页面轮询，并保留每次完整观测。"""
        runtime.write_image(self.folder / f'field_{self.index:03}.png', field)
        temporary = self.folder / 'preview.pending.jpg'
        runtime.write_image(temporary, cv2.resize(field, (1066, 600)), [cv2.IMWRITE_JPEG_QUALITY, 78])
        temporary.replace(self.folder / 'preview.jpg')

    def observe(self):
        """展开小地图后定位，计算期间发生场景变化则拒绝使用旧结果。"""
        from . import goto
        self.check()
        if self.win is None:
            self.win = runtime.Window()
            self.win.focus()
        self.identity(goto.capture_client(self.win))
        goto.map_open(self.win)
        observed = self.win.capture()
        self.index += 1
        report = self.localizer.locate(observed, f'observe_{self.index:03}')
        if np.mean(goto.mr.terrain(observed) != goto.mr.terrain(self.win.capture())) > .03:
            raise RuntimeError('定位期间地图发生变化，请重新开始测试。')
        if report.get('roi_to_map') is not None:
            report['enemy_markers'] = goto.normal_enemy_markers(observed, np.asarray(report['roi_to_map']))
        return report

    def plan(self, observation, target):
        """最小化后优先用箭头周期定位地面，再按当前地图投影换算落点。"""
        from . import goto
        from .movement_feedback import resolve_anchor
        goto.map_close(self.win)
        field = goto.capture_client(self.win)
        self.identity(field)
        self.check_collectible(field)
        self.preview(field)
        anchor = resolve_anchor(self, field, observation)
        click, length = movement_plan(self.localizer, observation, target, anchor)
        self.emit(road_remaining=length)
        return click

    def move(self, click):
        """仅向当前已验证窗口发送一次地面点击。"""
        x, y = self.win.gui.ClientToScreen(self.win.hwnd, (0, 0))
        self.win.handler.mouse_click(x + round(click[0]), y + round(click[1]))

    def pause(self, seconds):
        """等待期间保持取消和焦点检查。"""
        runtime.pause(seconds)

    def wait_stopped(self):
        """以紧凑小地图稳定性判断停稳，超时也不追加点击。"""
        from . import goto
        previous, stable = None, 0
        for index in range(60):
            self.pause(1)
            self.check()
            field = goto.capture_client(self.win)
            if goto.battle_popup_score(field) > .8:
                if self.request.get('purpose') == 'enemy':
                    from .movement_feedback import TargetTriggered
                    self.preview(field)
                    raise TargetTriggered('已接触敌人，战斗准备界面已出现。', {'kind': 'battle_popup'})
                raise RuntimeError('移动后出现战斗弹窗，测试停止。')
            self.check_collectible(field)
            road = goto.mr.terrain(field[123:250, 25:206])
            stable = stable + 1 if previous is not None and np.mean(road != previous) < .005 else 0
            previous = road
            if stable >= 3 and index >= 4:
                self.preview(field)
                return
        raise RuntimeError('等待小队停稳超时，未追加移动点击。')

    def finish_view(self):
        """返回紧凑小地图并保存终点，保留人工复核的画面。"""
        from . import goto
        goto.map_close(self.win)
        self.preview(goto.capture_client(self.win))


def main():
    """处理一次后台请求；任何退出路径先释放控制，再发布可供页面读取的终态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    folder = args.request.resolve().parent
    state = {'state': 'starting', 'message': '正在校验地图…', 'movement_clicks': 0}

    def emit(**values):
        """状态文件原子替换，避免 HTTP 读到半份 JSON。"""
        state.update(values, updated_at=time.time())
        pending = folder / 'status.pending.json'
        pending.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        pending.replace(folder / 'status.json')

    session = None
    lock = FileLock(settings.ROOT / 'log/campaign_prototype/manual_movement.lock')
    try:
        emit()
        lock.acquire(timeout=0)
        runtime.check_stop()
        request = json.loads(args.request.read_text(encoding='utf-8'))
        settings.output, settings.stop_file = folder, folder / 'STOP'
        settings.chapter, settings.difficulty = request['chapter'], request['difficulty']
        runtime.check_stop()
        settings.package, hashes = prepare(request, folder)
        emit(message='正在加载小队定位和章节识别…')
        metadata = json.loads((settings.package / 'map.json').read_text(encoding='utf-8'))
        if metadata.get('coordinate_model') == 'local_parallax':
            from .parallax_movement import ParallaxSessionMixin, run_movement
            class ParallaxSession(ParallaxSessionMixin, GameSession):
                pass
            session = ParallaxSession(request, hashes, folder, emit)
            result = run_movement(session)
        else:
            session = GameSession(request, hashes, folder, emit)
            result = navigate(session, request['target'], emit, arrival_radius=20, required_near=2,
                              purpose=request.get('purpose', 'position'))
        state.update(result)
    except KeyboardInterrupt as exc:
        state.update(state='cancelled', message=f'测试已停止：{exc}')
    except Timeout:
        state.update(state='failed', message='另一个标注窗口正在执行移动测试。')
    except Exception as exc:
        from .movement_feedback import TargetTriggered
        if isinstance(exc, TargetTriggered):
            state.update(state='triggered', message=str(exc), evidence=exc.evidence)
        else:
            traceback.print_exc()
            state.update(state='failed', message=f'移动测试失败：{exc}')
    finally:
        try:
            if session is not None and session.win is not None:
                session.win.close()
        except Exception as exc:
            traceback.print_exc()
            state.update(state='failed', message=f'释放游戏控制失败：{exc}')
        finally:
            lock.release()
            emit()
            (folder / 'result.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    if state['state'] == 'failed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
