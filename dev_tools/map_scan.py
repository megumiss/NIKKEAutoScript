"""编辑器的正式单章扫描任务，使用独立输出目录和后台进程。"""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading

if __package__:
    from .map_paths import DEFAULT_CAPTURE_ROOT, DEFAULT_MAPS_ROOT
else:
    from map_paths import DEFAULT_CAPTURE_ROOT, DEFAULT_MAPS_ROOT


ROOT = Path(__file__).resolve().parents[1]


class ScanJobs:
    """标注工具里的单章扫描任务：起子进程、汇报进度、采用或停止。

    同一时刻只允许一个扫描，因为扫描要独占游戏窗口和鼠标；任务状态会被 HTTP 线程并发访问，
    用可重入锁保护（start/stop 内部还会再调 status）。
    扫描本体跑在独立子进程里（dev_tools.minimap_chapters），日志进各自输出目录的 worker.log，
    这样标注工具崩了也不会把扫描带走，重启后还能从目录恢复现场。
    """

    def __init__(self, store):
        """绑定地图仓库，不启动任何进程。"""
        self.store = store
        self.lock = threading.RLock()
        self.job = None
        self.process = None

    def start(self, payload):
        """校验参数并起一个扫描子进程，返回初始状态。

        已有扫描在跑时直接报错而不是排队：两个扫描会互相抢游戏控制。
        输出目录放在采集根目录（而不是地图仓库）下的 scans/<随机 id>，避免扫描中间产物进入
        资源仓库；随机 id 同时用来防止前端拿旧 id 操作新任务。
        参数来自浏览器，类型要逐个核（注意 bool 是 int 的子类，所以用 type 判等而不是 isinstance）。
        """
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError('已有地图扫描正在运行。')
            if not isinstance(payload, dict):
                raise ValueError('扫描参数必须为对象。')
            chapter, process_3d = payload.get('chapter'), payload.get('process_3d')
            stroke = payload.get('stroke_px', 120)
            if type(chapter) is not int or not 1 <= chapter <= 99:
                raise ValueError('章节必须为 1～99 的整数。')
            if type(process_3d) is not bool:
                raise ValueError('请选择是否启用 3D 处理。')
            if type(stroke) not in (int, float) or not 0 < stroke <= 240:
                raise ValueError('扫描步长必须大于 0，且不超过 240。')
            # 3D 章节默认 40px 保存帧间距：39、40 章实测轨迹数翻倍、稠密视差更稳；平面章节沿用 80px。
            keyframe = payload.get('keyframe_px', 40 if process_3d else 80)
            if type(keyframe) not in (int, float) or not 0 < keyframe <= 240:
                raise ValueError('保存帧间距必须大于 0，且不超过 240。')
            identifier = secrets.token_hex(10)
            root = (DEFAULT_CAPTURE_ROOT if self.store.root.is_relative_to(DEFAULT_MAPS_ROOT.resolve())
                    else self.store.root)
            folder = root / 'scans' / identifier
            folder.mkdir(parents=True)
            command = [sys.executable, '-X', 'utf8', '-u', '-m', 'dev_tools.minimap_chapters',
                       '--start', str(chapter), '--end', str(chapter), '--output', str(folder),
                       '--driver-root', str(ROOT), '--stroke-px', str(stroke), '--keyframe-px', str(keyframe),
                       '--retries', '0']
            if process_3d:
                command.append('--process-3d')
            with (folder / 'worker.log').open('w', encoding='utf-8') as stream:
                self.process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.job = dict(id=identifier, chapter=chapter, process_3d=process_3d, stroke_px=stroke,
                            keyframe_px=keyframe, output=str(folder),
                            map_id=f'scans/{identifier}/chapter_{chapter:02d}')
            return self.status()

    def status(self):
        """汇总当前扫描的状态与给用户看的提示文本。

        状态由三处证据拼出来：子进程退出码、输出目录里的 STOP 标记，以及处理阶段文件。
        已采用的任务不再看进程和目录（副本已被删除），直接返回采用结论。
        退出码为 0 时顺手导出运行包并做一次加载校验，校验不过仍算失败——扫描进程自己不知道
        导出的包能不能被运行期接受。非零退出码则从 progress.json 里取真正的错误原因，
        退出码本身对使用者没有意义。
        帧数和阶段文件可能还没写出来，读失败都按「还没进展」处理，不影响整体状态。
        """
        with self.lock:
            if self.job is None:
                return {'state': 'idle', 'running': False, 'message': '先进入指定章节，再开始扫描。'}
            result = dict(self.job)
            if result.get('state') == 'adopted':
                result.update(running=False, frames=0, message=f"已采用为第 {result['chapter']} 章运行地图。",
                              log_path=str(Path(result['output']) / 'worker.log'))
                return result
            folder = Path(result['output'])
            package = folder / f'chapter_{result["chapter"]:02d}'
            code = self.process.poll()
            result.update(running=code is None, state='running', frames=0,
                          message='正在识别章节并扫描，请保持游戏在前台。', log_path=str(folder / 'worker.log'))
            try:
                scan = json.loads((package / 'source/scan.json').read_text(encoding='utf-8'))
                result['frames'] = len(scan.get('frames', []))
            except (OSError, ValueError):
                pass
            phases = {'layered': '正在进行 3D 分层重建…', 'loops': '正在用重访帧对闭环修正相机…',
                      'surfaces': '正在按高度连通聚合表面编号…', 'redraw': '正在按原始道路区域投影重绘…',
                      'export': '正在导出地图…', 'publishing': '正在发布地图…', 'complete': '处理完成。'}
            try:
                phase = json.loads((package / 'processing_status.json').read_text(encoding='utf-8'))['phase']
                result['message'] = phases.get(phase, result['message'])
            except (OSError, ValueError, KeyError):
                pass
            if (folder / 'STOP').exists():
                result.update(state='stopping' if code is None else 'cancelled',
                              message='正在安全停止，请等待释放游戏控制…' if code is None else '扫描已停止，原始帧已保留。')
            elif code is not None:
                result.update(state='failed', message=f'扫描未完成（退出码 {code}），请查看日志。')
                if code == 0:
                    try:
                        destination = self.store.root / result['map_id']
                        if package != destination and not destination.exists():
                            if __package__:
                                from .map_runtime import export_runtime
                            else:
                                from map_runtime import export_runtime
                            export_runtime(package, destination)
                        self.store.load(result['map_id'])
                        result.update(state='complete', message='扫描与处理完成，可以打开新地图。')
                    except (OSError, ValueError, KeyError) as exc:
                        result['message'] = f'地图导出校验失败：{exc}'
                else:
                    try:
                        progress = json.loads((folder / 'progress.json').read_text(encoding='utf-8'))
                        last = progress[-1]
                        error = last.get('error') or last.get('attempts', [{}])[-1].get('error')
                        if error:
                            result['message'] = str(error)
                    except (OSError, ValueError, IndexError, KeyError):
                        pass
            return result

    def adopt(self, identifier):
        """把已完成的扫描采用为本章运行地图：旧运行包备份到本地历史目录，再移除 scans 下的中间副本。"""
        with self.lock:
            if self.job is None or identifier != self.job['id']:
                raise ValueError('扫描任务已更换，请刷新状态。')
            if self.process.poll() is None:
                raise ValueError('扫描尚未结束。')
            if self.status().get('state') != 'complete':
                raise ValueError('只有处理完成且校验通过的扫描才能采用。')
            if __package__:
                from .map_runtime import adopt_runtime
            else:
                from map_runtime import adopt_runtime
            source = self.store.root / self.job['map_id']
            chapter = self.job['chapter']
            result = adopt_runtime(source, chapter, self.store.root)
            map_id = f'chapter_{chapter:02d}'
            self.store.load(map_id)
            # 采用后 scans 下的副本与运行包重复，完整采集仍在 captures 目录，删除副本避免双份进入版本库。
            shutil.rmtree(source.parent, ignore_errors=True)
            self.job.update(state='adopted', map_id=map_id)
            return dict(state='adopted', message=f'已采用为第 {chapter} 章运行地图，旧包已备份。', map_id=map_id,
                        **result)

    def stop(self, identifier):
        """请求停止扫描：只放一个 STOP 标记文件，不杀进程。

        扫描进程正占着鼠标和游戏窗口，强杀会把游戏留在被接管的状态；标记文件让它跑到检查点
        自行释放控制并保留已采集的原始帧。identifier 不匹配说明前端状态过期，拒绝操作。
        """
        with self.lock:
            if self.job is None or identifier != self.job['id']:
                raise ValueError('扫描任务已更换，请刷新状态。')
            if self.process.poll() is None:
                (Path(self.job['output']) / 'STOP').touch()
            return self.status()

    def close(self):
        """标注工具退出时收尾：先礼后兵地结束扫描进程。

        先走 STOP 让它自己释放游戏控制，等 10 秒；超时才 terminate 再等 5 秒。
        直接 terminate 会让游戏停在被接管的状态，需要人工点一下才能恢复操作。
        """
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return
            self.stop(self.job['id'])
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
