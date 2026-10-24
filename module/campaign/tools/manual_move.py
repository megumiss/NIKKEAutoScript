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

from module.campaign.navigation.movement import (
    movement_plan as movement_plan,
    navigate as navigate,
)
from module.campaign.navigation.session import NavigationSession
from module.campaign.tools import runtime, settings
from module.campaign.tools.map_package import MapPackage


def sha(path):
    """绑定完整文件内容，避免新标注套用旧底图坐标。

    读取 path 指向文件的全部字节并返回 SHA-256 十六进制字符串。
    用于请求版本和运行中资源身份校验；文件读取错误直接传播，不能把缺失文件视为未变化。
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(request, folder):
    """准备绑定请求版本的运行地图，校验道路目标和对应场景标定。

    request 绑定地图图像、标注版本、章节、难度和目标；返回实际运行包路径与源文件哈希。
    平面包复制到任务目录并生成校验清单，标定共用 assets/calibration.json。
    """
    source = Path(request['package'])
    if request.get('action', 'move') != 'move':
        raise ValueError('未知移动操作。')
    if sha(source / 'map.png') != request['image_sha256'] or sha(
            source / 'annotations.json') != request['revision']:
        raise ValueError('地图或标注已变化，请重新加载后选择目标。')
    metadata = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    if metadata.get('coordinate_model') is not None:
        raise ValueError('只支持平面地图。')
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
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'vision/assets/calibration.json', destination / 'calibration.json')
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






class GameSession(NavigationSession):
    """一次人工移动请求的会话：持有定位器、OCR 与受控窗口，逐步观测并追加移动。

    窗口延迟到首次 observe 才创建，构造阶段失败就不会留下占用鼠标的实例。
    """

    def __init__(self, request, hashes, folder, emit):
        """初始化定位器、OCR 与请求上下文，窗口由首次观测延迟创建。

        保存请求、源文件哈希、输出目录和状态回调。
        OCR 使用本地 CPU 模型；此处不取得窗口，首次 observe 才创建控制实例，初始化失败不会遗留鼠标占用。
        """
        from paddleocr import TextRecognition
        from module.campaign.tools.adaptive import AdaptiveLocalizer
        self.model = TextRecognition(model_name='PP-OCRv5_mobile_rec',
                                     model_dir=str(settings.ROOT / 'bin/paddleocr/PP-OCRv5_mobile_rec_infer'),
                                     device='cpu', cpu_threads=2)
        self.localizer = AdaptiveLocalizer()
        self.request, self.hashes, self.folder, self.emit = request, hashes, folder, emit
        self.package = Path(request['package'])
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
        from module.campaign.vision.chapter import chapter_number
        from module.campaign.tools import goto
        if goto.battle_popup_score(field) > .8:
            from module.campaign.navigation.movement_feedback import TargetTriggered
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
        from module.campaign.navigation.movement_feedback import collectible_indicator, collectible_counter, TargetTriggered
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
        from module.campaign.tools import goto
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
        if np.mean(goto.terrain(observed) != goto.terrain(self.win.capture())) > .03:
            raise RuntimeError('定位期间地图发生变化，请重新开始测试。')
        if report.get('roi_to_map') is not None:
            report['enemy_markers'] = goto.normal_enemy_markers(observed, np.asarray(report['roi_to_map']))
        return report


    def move(self, click):
        """仅向当前已验证窗口发送一次地面点击。

        click 是客户区像素，按窗口屏幕原点转换并四舍五入后发送一次点击。
        实际焦点、取消和驱动错误检查由窗口的 GuardedInput 执行；本方法不判断是否到达。
        """
        from module.campaign.tools import goto
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
        session = GameSession(request, hashes, folder, emit)
        annotations = json.loads((settings.package / 'annotations.json').read_text(encoding='utf-8'))
        result = navigate(session, request['target'], emit, required_near=2,
                          purpose=request.get('purpose', 'position'),
                          elevators=annotations if annotations.get('connections') else None)
        state.update(result)
    except KeyboardInterrupt as exc:
        state.update(state='cancelled', message=f'测试已停止：{exc}')
    except Timeout:
        state.update(state='failed', message='另一个标注窗口正在执行移动测试。')
    except Exception as exc:
        from module.campaign.navigation.movement_feedback import TargetTriggered
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
