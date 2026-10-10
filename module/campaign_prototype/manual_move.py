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
    """绑定完整文件内容，避免新标注套用旧底图坐标。

    读取 path 指向文件的全部字节并返回 SHA-256 十六进制字符串。
    用于请求版本和运行中资源身份校验；文件读取错误直接传播，不能把缺失文件视为未变化。
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(request, folder):
    """准备绑定请求版本的运行地图，校验道路目标和对应场景标定。

    request 绑定地图图像、标注版本、章节、难度和目标；返回实际运行包路径与源文件哈希。
    平面包复制到任务目录并生成校验清单，local_parallax 包只核对修订道路上的目标，标定在移动时临时生成。
    """
    source = Path(request['package'])
    if request.get('action', 'move') not in ('move', 'calibrate'):
        raise ValueError('未知移动操作。')
    if sha(source / 'map.png') != request['image_sha256'] or sha(
            source / 'annotations.json') != request['revision']:
        raise ValueError('地图或标注已变化，请重新加载后选择目标。')
    metadata = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    if metadata.get('coordinate_model') == 'local_parallax':
        from .parallax_localizer import ParallaxLocalizer
        if metadata['chapter'] != request['chapter']:
            raise ValueError('所选地图章节已变化。')
        localizer = ParallaxLocalizer(source)
        if request.get('action', 'move') != 'calibrate':
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
    if (source / 'connectivity.json').exists():
        names += ('connectivity.json',)
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
    """验证道路连通性并生成画面内完整目标的落点与路线长度。

    先在修订道路上验证连通性，再把完整 target 投影到 ROI 和场景安全区域。
    返回客户区点击点与道路路径长度；离屏目标抛出 ValueError，供调用者选择镜头规划流程。
    """
    from .camera_navigation import field_chart, target_projection, inside, FIELD_BOUNDS, ROI_BOUNDS
    position = np.asarray(observation['position'], float)
    _, path = localizer.route(position, target)
    if np.linalg.norm(np.asarray(target) - position) < 2:
        raise ValueError('当前道路没有可用的前进落点，停止测试。')
    roi, click = target_projection(field_chart(localizer, observation, anchor), observation, target)
    if not inside(roi, ROI_BOUNDS) or not inside(click, FIELD_BOUNDS):
        raise ValueError('目标不在当前有效画面内，需要先平移镜头。')
    return click, float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())


def navigate(session, target, emit, max_moves=24, arrival_radius=12, required_near=3, purpose='position', elevators=None):
    """有限步执行并重新定位，连续三次近点观测才算到达，无进展或预算耗尽明确失败。

    session 提供观测、规划、输入及停稳接口，target、到达半径和位置差都使用原图像素。
    到点模式按 required_near 次连续近点观测确认；收集品和敌人模式在附近进行有限试点，耗尽后返回 needs_review。
    每次点击后等待停稳再定位；无进展、max_moves 用尽或十分钟超时抛错，实际触发由会话异常传给入口处理。
    镜头平移导致小队观测变化时丢弃旧基准，最多两次等待停稳并重新定位，不使用旧计划点击。
    平面到点模式可传入已校验的电梯标注；入口仅作为中转，连续两次确认出口区域后继续最终目标。
    """
    from .movement_feedback import (AnchorUnresolved, SquadPositionChanged, road_recovery_click, resolve_anchor,
                                    wake_arrow_click)

    target = np.asarray(target, float)
    clicks, near, stagnant, replans = 0, 0, 0, 0
    previous = None
    probes = None
    probe_index = 0
    arrow_recoveries = 0
    legs, transfers = None, []
    exit_near, entry_waits, leg_start_clicks = 0, 0, 0
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        session.check()
        emit(state='locating', message='正在定位小队…', movement_clicks=clicks)
        observation = session.observe()
        position = np.asarray(observation['position'], float)
        if (observation.get('position_kind') != 'squad' or position.shape != (2,)
                or not np.isfinite(position).all()):
            raise RuntimeError('小队定位无效，停止移动。')
        if elevators is not None and purpose == 'position':
            from .elevator_navigation import at_elevator_exit, plan_elevators
            if legs is None:
                legs = plan_elevators(session.localizer, elevators, position, target)
                emit(elevator_route=legs.copy(), elevator_transfers=[])
            if legs and at_elevator_exit(session.localizer, position, legs[0]):
                exit_near += 1
                emit(state='verifying', message=f'正在确认电梯出口（{exit_near}/2）…',
                     position=position.tolist(), distance=float(np.linalg.norm(target - position)),
                     iou=observation['iou'])
                if exit_near < 2:
                    session.pause(1)
                    continue
                transfers.append(dict(legs.pop(0), position=position.tolist()))
                emit(message='已确认电梯传送，继续前往目标。', elevator_transfers=transfers.copy(),
                     elevator_entry=None, elevator_exit=None)
                session.anchor_reference = None
                session.anchor_reference_method = None
                previous, probes, probe_index, stagnant, near = None, None, 0, 0, 0
                exit_near, entry_waits, leg_start_clicks = 0, 0, clicks
                continue
            exit_near = 0
        transferring = bool(legs)
        approach = target
        if transferring:
            approach = np.asarray(legs[0]['entry'], float)
        if purpose == 'enemy':
            markers = [np.asarray(p) for p in observation.get('enemy_markers', [])
                       if np.linalg.norm(np.asarray(p) - target) <= 30]
            if len(markers) == 1:
                approach = markers[0]
                emit(target_marker=approach.tolist())
        distance = float(np.linalg.norm(approach - position))
        emit(position=position.tolist(), distance=float(np.linalg.norm(target - position)) if transferring else distance,
             iou=observation['iou'])
        if distance <= arrival_radius and purpose == 'position' and not transferring:
            if elevators is not None:
                session.localizer.route(position, target)
            near += 1
            if near >= required_near:
                session.finish_view()
                return {'state': 'arrived', 'message': f'已到达目标附近（连续 {required_near} 次在 {arrival_radius} 地图像素内）。',
                        'movement_clicks': clicks, 'position': position.tolist(), 'distance': distance}
            emit(state='verifying', message=f'正在复核到达位置（{near}/{required_near}）…')
            session.pause(1)
            continue
        near = 0
        if transferring:
            emit(message='正在前往电梯入口并等待传送…', elevator_entry=legs[0]['entry'],
                 elevator_exit=legs[0]['exit'], elevator_distance=distance)
            if clicks - leg_start_clicks >= 3:
                raise RuntimeError('已尝试三次前往电梯入口，尚未确认传送，停止移动。')
            if distance < 2:
                entry_waits += 1
                if entry_waits > 3:
                    raise RuntimeError('已在电梯入口停稳，但未触发传送，停止移动。')
                session.pause(2)
                continue
        if previous is not None and probes is None:
            stagnant = stagnant + 1 if np.linalg.norm(position - previous) < 3 else 0
            if stagnant >= 2:
                raise RuntimeError('连续移动后小队没有明显位移，停止测试。')
        if clicks >= max_moves:
            raise RuntimeError(f'已达到 {max_moves} 次移动上限，尚未到达目标。')
        emit(state='planning', message='正在自动换算道路落点…')
        try:
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
                    except AnchorUnresolved:
                        raise
                    except ValueError as exc:
                        emit(message=f'附近落点不可用：{exc}')
                if click is None:
                    session.finish_view()
                    return dict(state='needs_review', message='已到目标附近，有限范围尝试后仍未确认触发，请检查现场。',
                                movement_clicks=clicks, position=position.tolist(), distance=distance)
                emit(state='probing', message=f'正在尝试目标附近落点（{probe_index}/{len(probes)}）…')
            else:
                click = session.plan(observation, approach)
        except AnchorUnresolved:
            if arrow_recoveries:
                raise
            try:
                click = road_recovery_click(session, observation, approach)
            except ValueError:
                click = wake_arrow_click(session, observation)
            session.check()
            session.move(click)
            clicks += 1
            arrow_recoveries += 1
            emit(state='moving', movement_clicks=clicks, arrow_recoveries=arrow_recoveries,
                 click=click.tolist(), message='正在沿道路短移，停稳后重新识别箭头…')
            session.wait_stopped()
            updated = session.observe()
            from . import goto
            goto.map_close(session.win)
            field = goto.capture_client(session.win)
            session.identity(field)
            session.check_collectible(field)
            session.preview(field)
            resolve_anchor(session, field, updated, allow_scene=False)
            previous, probes, probe_index, stagnant, near = None, None, 0, 0, 0
            continue
        except SquadPositionChanged as error:
            replans += 1
            emit(state='locating', message='镜头平移后小队位置变化，正在停稳并重新定位…',
                 camera_replans=replans, squad_before_pan=error.before, squad_after_pan=error.after)
            if replans > 2:
                raise RuntimeError('镜头平移后小队位置反复变化，已用尽两次重新定位机会；未追加移动点击。') from error
            session.anchor_reference = None
            session.anchor_reference_method = None
            previous, probes, probe_index, stagnant, near = None, None, 0, 0, 0
            session.wait_stopped()
            continue
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
        """初始化定位器、OCR 与请求上下文，窗口由首次观测延迟创建。

        保存请求、源文件哈希、输出目录和状态回调，按 coordinate_model 选择定位器。
        OCR 使用本地 CPU 模型；此处不取得窗口，首次 observe 才创建控制实例，初始化失败不会遗留鼠标占用。
        """
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
        """同时尊重任务停止文件与原型全局停止文件，源地图变化也禁止继续输入。

        检查任务 STOP、全局 STOP、已创建窗口状态及源地图文件哈希。
        停止抛出 KeyboardInterrupt，资源变化抛出 RuntimeError；调用者应在观测或发送输入前调用。
        """
        runtime.check_stop()
        if (settings.ROOT / 'log/campaign_prototype/STOP').exists():
            raise KeyboardInterrupt('原型全局 STOP 已生效。')
        if self.win is not None:
            self.win.check()
        for name, expected in self.hashes.items():
            if sha(Path(self.request['package']) / name) != expected:
                raise RuntimeError('地图或标注在测试期间变化，停止移动。')

    def identity(self, field):
        """核对当前章节、难度和战斗弹窗，不自动切章或进入战斗。

        field 使用完整客户区 BGR 图，章节号及 NORMAL/HARD 文字必须与请求一致。
        敌人模式保留普通准备弹窗、关闭并排除 EX，再携带证据抛出 TargetTriggered；关闭失败按错误处理。
        """
        from dev_tools.minimap_chapters import chapter_number
        from . import goto
        if goto.battle_popup_score(field) > .8:
            from .movement_feedback import TargetTriggered
            if self.request.get('purpose') == 'enemy':
                self.preview(field)
                evidence = []
                status = goto.check_battle_popup(self.win, evidence, self.index, 'enemy', image=field)
                if status == 'ex_stage_skipped':
                    raise TargetTriggered('已排除 EX 关卡并关闭弹窗，本次移动已停止。',
                                          dict(kind=status, **evidence[-1]))
                raise TargetTriggered('已接触敌人，战斗准备界面已出现。', {'kind': 'battle_popup'})
            raise RuntimeError('出现战斗弹窗，移动测试已停止。')
        # 移动后的个别帧里章节／难度控件可能处于动画或遮挡状态，允许短暂重试再判失败。
        for _ in range(3):
            if chapter_number(field, self.model) == self.request['chapter']:
                break
            time.sleep(0.5)
            field = goto.capture_client(self.win)
        else:
            raise RuntimeError(f"游戏章节与所选第 {self.request['chapter']} 章不符，或章节号无法识别。")
        for _ in range(3):
            crop = cv2.resize(field[898:912, 1613:1670], None, fx=4, fy=4)
            text = next(iter(self.model.predict(crop)))
            if text['rec_text'].strip().upper() == self.request['difficulty'].upper() and text['rec_score'] >= .9:
                break
            time.sleep(0.5)
            field = goto.capture_client(self.win)
        else:
            raise RuntimeError('游戏普通／困难难度与所选目标不符，或难度无法识别。')

    def check_collectible(self, field):
        """收集品橙色倒三角出现即停止；自动拾取导致计数增加时也保留成功反馈。

        提示出现立即抛出 TargetTriggered；未见提示时才检查计数，同一总数下连续两次增加作为拾取反馈。
        """
        if self.request.get('purpose') != 'collectible':
            return
        from .movement_feedback import collectible_indicator, collectible_counter, TargetTriggered
        indicator = collectible_indicator(field)
        if indicator is not None:
            self.preview(field)
            raise TargetTriggered('已发现收集品橙色倒三角，移动测试已停止。', indicator)
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
        """原子更新压缩截图供页面轮询，并保留每次完整观测。

        保存按观测序号命名的原始 PNG，同时生成 1066×600 的 JPEG 预览。
        预览通过临时文件替换发布，避免页面轮询读到未完成图像；写盘错误向上层传播。
        """
        runtime.write_image(self.folder / f'field_{self.index:03}.png', field)
        temporary = self.folder / 'preview.pending.jpg'
        runtime.write_image(temporary, cv2.resize(field, (1066, 600)), [cv2.IMWRITE_JPEG_QUALITY, 78])
        temporary.replace(self.folder / 'preview.jpg')

    def observe(self):
        """展开小地图后定位，计算期间发生场景变化则拒绝使用旧结果。

        首次调用时创建并聚焦窗口，核对章节后回正、展开小地图并保存定位证据。
        计算后比较新旧道路掩码，变化超过 3% 即拒绝结果；有效报告附加同帧普通敌人地图坐标。
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
        observed = self.win.capture()
        self.index += 1
        report = self.localizer.locate(observed, f'observe_{self.index:03}')
        if np.mean(goto.mr.terrain(observed) != goto.mr.terrain(self.win.capture())) > .03:
            raise RuntimeError('定位期间地图发生变化，请重新开始测试。')
        if report.get('roi_to_map') is not None:
            report['enemy_markers'] = goto.normal_enemy_markers(observed, np.asarray(report['roi_to_map']))
        return report

    def plan(self, observation, target):
        """道路只检查连通性；优先平移到完整目标后交给游戏自动寻路。

        用道路路径验证小队到 target 的连通性，并发布剩余道路长度。
        随后交给共享镜头规划器返回客户区落点；规划可能平移镜头，小队移动点击仍由 move 执行。
        """
        from .camera_navigation import plan_world_move
        _, path = self.localizer.route(np.asarray(observation['position']), np.asarray(target))
        self.emit(road_remaining=float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum()))
        return plan_world_move(self, observation, target)

    def move(self, click):
        """仅向当前已验证窗口发送一次地面点击。

        click 是客户区像素，按窗口屏幕原点转换并四舍五入后发送一次点击。
        实际焦点、取消和驱动错误检查由窗口的 GuardedInput 执行；本方法不判断是否到达。
        """
        from . import goto
        self.check()
        field = goto.capture_client(self.win)
        self.identity(field)
        self.check_collectible(field)
        x, y = self.win.gui.ClientToScreen(self.win.hwnd, (0, 0))
        self.win.handler.mouse_click(x + round(click[0]), y + round(click[1]))

    def pause(self, seconds):
        """提供可取消的等待，并检查活动窗口状态。

        等待 seconds 秒并复用 runtime 的短周期停止检查。
        存在活动窗口时同时检查窗口状态，取消或失焦会中断等待而不是继续后续点击。
        """
        runtime.pause(seconds)

    def wait_stopped(self):
        """调用共享停稳检测，等待紧凑地图的道路和小队圆环稳定。

        无返回值；超时和身份异常向 navigate 传播，阻止在上一段运动未结束时叠加输入。
        """
        from .camera_navigation import wait_for_squad
        wait_for_squad(self)

    def finish_view(self):
        """返回紧凑小地图并保存终点，保留人工复核的画面。

        把小地图恢复为紧凑状态，再通过 preview 保存终点客户区图像。
        用于到达或需要复核的终态，截图本身不构成拾取成功或战斗完成的证据。
        """
        from . import goto
        goto.map_close(self.win)
        self.preview(goto.capture_client(self.win))


def main():
    """处理一次后台请求；任何退出路径先释放控制，再发布可供页面读取的终态。

    从 --request 指定文件读取一次请求，取得跨进程文件锁并按地图类型启动移动或标定。
    取消、触发和失败分别写入终态；finally 释放窗口及锁后发布 status.json 和 result.json，失败退出码为 1。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    folder = args.request.resolve().parent
    state = {'state': 'starting', 'message': '正在校验地图…', 'movement_clicks': 0}

    def emit(**values):
        """状态文件原子替换，避免 HTTP 读到半份 JSON。

        把增量字段合并到共享状态并记录 updated_at。
        先完整写 status.pending.json 再原子替换 status.json；不清空未更新字段，便于页面轮询累计次数与证据。
        """
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
            annotations = json.loads((settings.package / 'annotations.json').read_text(encoding='utf-8'))
            result = navigate(session, request['target'], emit, arrival_radius=20, required_near=2,
                              purpose=request.get('purpose', 'position'),
                              elevators=annotations if annotations.get('connections') else None)
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
