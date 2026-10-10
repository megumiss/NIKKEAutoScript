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
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.job = None
        self.process = None

    def start(self, payload):
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
        with self.lock:
            if self.job is None or identifier != self.job['id']:
                raise ValueError('扫描任务已更换，请刷新状态。')
            if self.process.poll() is None:
                (Path(self.job['output']) / 'STOP').touch()
            return self.status()

    def close(self):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return
            self.stop(self.job['id'])
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
