"""标注工具的单任务移动控制；设备输入始终在独立子进程中执行。"""

import copy
import hashlib
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
        """绑定标注目录与独立日志目录，避免请求指定任意命令或输出路径。

        store 负责可访问地图的身份解析，output 默认使用原型日志下的独立任务目录。
        初始化锁和进程状态，不加载图像模型或连接设备；请求不能选择可执行命令。
        """
        self.store = store
        self.output = Path(output or ROOT / 'log/campaign_prototype/manual_movement')
        self.lock = threading.RLock()
        self.job = None
        self.process = None

    def start(self, payload):
        """冻结目标坐标和地图版本，同一服务只运行一个测试，立即返回任务状态。

        校验地图版本、目标原图坐标、难度及行为类型，移动请求要求标定状态为 shared 或 ready。
        冻结 request.json 后启动隐藏的独立 Python 子进程，返回任务副本；设备控制和最终状态由子进程负责。
        """
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError('已有移动测试正在运行，请等待结束或先停止测试。')
            loaded = self.store.load(payload['id'])
            action = payload.get('action', 'move')
            if action not in ('move', 'calibrate'):
                raise ValueError('未知移动操作。')
            if (payload.get('revision') != loaded['revision']
                    or payload.get('image_sha256') != loaded['annotations']['image_sha256']):
                raise ValueError('地图或标注已变化，请重新加载后选择目标。')
            target = payload.get('target')
            if action == 'calibrate':
                metadata = json.loads((Path(loaded['path']) / 'map.json').read_text(encoding='utf-8'))
                if metadata.get('coordinate_model') != 'local_parallax':
                    raise ValueError('此采集入口用于分层地图。')
                target = [0, 0]
            if (not isinstance(target, list) or len(target) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v)
                           or not 0 <= v < bound for v, bound in zip(target, loaded['size']))):
                raise ValueError('目标必须是当前底图范围内的原始像素坐标。')
            difficulty = payload.get('difficulty')
            if difficulty not in ('normal', 'hard'):
                raise ValueError('请选择本次测试的普通或困难难度。')
            purpose = payload.get('purpose', 'position')
            if purpose not in ('position', 'collectible', 'enemy'):
                raise ValueError('未知到点行为。')
            auto_calibrate = payload.get('auto_calibrate', False)
            if type(auto_calibrate) is not bool:
                raise ValueError('自动标定选项必须为布尔值。')
            if action == 'move':
                calibration = self.calibration(loaded['id'], difficulty)
                if calibration['state'] not in ('shared', 'ready'):
                    raise ValueError(calibration['message'])
                auto_calibrate = False
            identifier = secrets.token_hex(12)
            folder = self.output / identifier
            folder.mkdir(parents=True)
            request = {'package': loaded['path'], 'chapter': loaded['chapter'], 'difficulty': difficulty,
                       'target': target, 'map_id': loaded['id'], 'revision': loaded['revision'],
                       'image_sha256': loaded['annotations']['image_sha256'], 'action': action, 'purpose': purpose,
                       'auto_calibrate': auto_calibrate}
            (folder / 'request.json').write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            job = {'id': identifier, 'map_id': loaded['id'], 'target': target, 'chapter': loaded['chapter'],
                   'difficulty': difficulty, 'state': 'starting', 'message': '正在检查地图与移动环境…',
                   'created_at': time.time(), 'movement_clicks': 0, 'action': action}
            command = [sys.executable, '-X', 'utf8', '-u', '-m', 'module.campaign_prototype.manual_move',
                       '--request', str((folder / 'request.json').resolve())]
            with (folder / 'worker.log').open('w', encoding='utf-8') as stream:
                self.process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.job = job
            return copy.deepcopy(job)

    def calibration(self, identifier, difficulty):
        """查询移动标定状态；分层地图在移动任务内自动做两点临时标定，不再读取保存的标定文件。

        平面地图返回 shared，分层地图返回 ready 并说明临时标定方式；查询只读元数据，不取得游戏控制。
        """
        if difficulty not in ('normal', 'hard'):
            raise ValueError('请选择普通或困难难度。')
        loaded = self.store.load(identifier)
        metadata = json.loads((Path(loaded['path']) / 'map.json').read_text(encoding='utf-8'))
        if metadata.get('coordinate_model') is None:
            return dict(state='shared', message='平面地图统一使用第 38 章现用标定，不限制章节。')
        if metadata.get('coordinate_model') != 'local_parallax':
            return dict(state='unsupported', message='非平面地图移动待定，尚无可用的移动标定。')
        surfaces = len(metadata.get('surfaces') or [])
        return dict(state='ready', temporary=True,
                    message='分层地图在移动开始时自动做两次正交短停靠的临时标定，仅本次任务有效，不写入地图包。',
                    detail=f'表面编号 {surfaces} 个，同层道路按表面并集判定' if surfaces else '包未聚合表面编号，同层道路按观测帧的局部平面判定')

    def status(self):
        """读取原子发布的状态，进程退出而没有终态时也不能误报成功。

        把子进程原子发布的 status.json 合并到任务副本，并补充运行状态、预览和日志路径。
        进程已退出却缺少终态时返回失败；运行超过 660 秒会请求 STOP，不把进程退出等同于到达。
        """
        with self.lock:
            if self.job is None:
                return {'state': 'idle', 'message': '选择目标点后开始移动测试。'}
            folder = self.output / self.job['id']
            state = folder / 'status.json'
            result = copy.deepcopy(self.job)
            if state.exists():
                result.update(json.loads(state.read_text(encoding='utf-8')))
            code = self.process.poll()
            if code is not None and result['state'] not in ('arrived', 'calibrated', 'triggered', 'needs_review',
                                                          'failed', 'cancelled'):
                result.update(state='failed', message=f'移动进程异常结束（{code}），请查看运行日志。')
            if code is None and time.time() - self.job['created_at'] > 660:
                self.stop(self.job['id'])
            result['running'] = code is None
            result['preview'] = (folder / 'preview.jpg').exists()
            result['log_path'] = str(folder / 'worker.log')
            return result

    def stop(self, identifier):
        """通过任务专用 STOP 协作退出；不清除其他原型的停止文件。

        identifier 必须匹配本服务正在管理的任务，仍在运行时写入该任务目录的 STOP。
        返回 stopping 状态而非承诺已释放；游戏已经接受的寻路可能继续，其他任务停止文件不受影响。
        """
        with self.lock:
            if self.job is None or identifier != self.job['id']:
                raise ValueError('移动测试已更换，请刷新状态。')
            if self.process.poll() is None:
                (self.output / identifier / 'STOP').touch()
            return {'state': 'stopping', 'message': '正在停止测试并释放控制；游戏已开始的寻路可能继续。'}

    def preview(self, identifier):
        """只提供当前任务的压缩截图，不接受客户端文件路径。

        仅接受当前任务 ID，返回其 preview.jpg 的字节内容。
        不接受任意文件路径；任务不匹配或截图尚不存在时向调用者报告错误。
        """
        with self.lock:
            if self.job is None or identifier != self.job['id']:
                raise ValueError('没有这次移动测试的截图。')
            return (self.output / identifier / 'preview.jpg').read_bytes()

    def close(self):
        """关闭服务时先请求子进程释放，异常挂起再回收子进程。

        服务关闭时对运行中的子进程发送 STOP，并等待五秒完成协作释放。
        超时后终止并再次等待；无进程或已退出时立即返回，不删除证据目录。
        """
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return
            self.stop(self.job['id'])
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
