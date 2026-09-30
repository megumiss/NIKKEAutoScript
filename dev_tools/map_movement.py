"""标注工具的单任务移动控制；设备输入始终在独立子进程中执行。"""

import copy
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]


class MovementJobs:
    def __init__(self, store, output=None):
        """绑定标注目录与独立日志目录，避免请求指定任意命令或输出路径。"""
        self.store = store
        self.output = Path(output or ROOT / 'log/campaign_prototype/manual_movement')
        self.lock = threading.RLock()
        self.job = None
        self.process = None

    def start(self, payload):
        """冻结目标坐标和地图版本，同一服务只运行一个测试，立即返回任务状态。"""
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError('已有移动测试正在运行，请等待结束或先停止测试。')
            loaded = self.store.load(payload['id'])
            if (payload.get('revision') != loaded['revision']
                    or payload.get('image_sha256') != loaded['annotations']['image_sha256']):
                raise ValueError('地图或标注已变化，请重新加载后选择目标。')
            target = payload.get('target')
            if (not isinstance(target, list) or len(target) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v)
                           or not 0 <= v < bound for v, bound in zip(target, loaded['size']))):
                raise ValueError('目标必须是当前底图范围内的原始像素坐标。')
            difficulty = payload.get('difficulty')
            if difficulty not in ('normal', 'hard'):
                raise ValueError('请选择本次测试的普通或困难难度。')
            identifier = secrets.token_hex(12)
            folder = self.output / identifier
            folder.mkdir(parents=True)
            request = {'package': loaded['path'], 'chapter': loaded['chapter'], 'difficulty': difficulty,
                       'target': target, 'map_id': loaded['id'], 'revision': loaded['revision'],
                       'image_sha256': loaded['annotations']['image_sha256']}
            (folder / 'request.json').write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            job = {'id': identifier, 'map_id': loaded['id'], 'target': target, 'chapter': loaded['chapter'],
                   'difficulty': difficulty, 'state': 'starting', 'message': '正在检查地图与移动环境…',
                   'created_at': time.time(), 'movement_clicks': 0}
            command = [sys.executable, '-X', 'utf8', '-u', '-m', 'module.campaign_prototype.manual_move',
                       '--request', str((folder / 'request.json').resolve())]
            with (folder / 'worker.log').open('w', encoding='utf-8') as stream:
                self.process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.job = job
            return copy.deepcopy(job)

    def status(self):
        """读取原子发布的状态，进程退出而没有终态时也不能误报成功。"""
        with self.lock:
            if self.job is None:
                return {'state': 'idle', 'message': '选择目标点后开始移动测试。'}
            folder = self.output / self.job['id']
            state = folder / 'status.json'
            result = copy.deepcopy(self.job)
            if state.exists():
                result.update(json.loads(state.read_text(encoding='utf-8')))
            code = self.process.poll()
            if code is not None and result['state'] not in ('arrived', 'failed', 'cancelled'):
                result.update(state='failed', message=f'移动进程异常结束（{code}），请查看运行日志。')
            if code is None and time.time() - self.job['created_at'] > 660:
                self.stop(self.job['id'])
            result['running'] = code is None
            result['preview'] = (folder / 'preview.jpg').exists()
            result['log_path'] = str(folder / 'worker.log')
            return result

    def stop(self, identifier):
        """通过任务专用 STOP 协作退出；不清除其他原型的停止文件。"""
        with self.lock:
            if self.job is None or identifier != self.job['id']:
                raise ValueError('移动测试已更换，请刷新状态。')
            if self.process.poll() is None:
                (self.output / identifier / 'STOP').touch()
            return {'state': 'stopping', 'message': '正在停止测试并释放控制；游戏已开始的寻路可能继续。'}

    def preview(self, identifier):
        """只提供当前任务的压缩截图，不接受客户端文件路径。"""
        with self.lock:
            if self.job is None or identifier != self.job['id']:
                raise ValueError('没有这次移动测试的截图。')
            return (self.output / identifier / 'preview.jpg').read_bytes()

    def close(self):
        """关闭服务时先请求子进程释放，异常挂起再回收子进程。"""
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return
            self.stop(self.job['id'])
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
