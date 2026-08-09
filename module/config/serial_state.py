"""
多实例顺序执行（串行令牌）的共享状态与配置读取。

- 配置在 config/deploy.yaml 的 Serial 节（SerialEnable / SerialGroup /
  SerialOnError / SerialIdleThreshold），由本模块直接读取，避免实例进程
  实例化 DeployConfig（其 __init__ 会重写 deploy.yaml）。
- 运行状态在 ./config/serial/state.json，由后端编排器与各实例进程共同
  读写，所有访问经 FileLock 保护，写入采用临时文件 + replace 保证原子性。
- 状态文件必须放在 ./config 的子目录内：nkas_instance() 会把 ./config
  根目录下的所有 *.json 当作实例配置。
"""

import json
import os
import re
import time
from datetime import datetime, timedelta

from filelock import FileLock

from deploy.utils import poor_yaml_read

STATE_FILE = './config/serial/state.json'
STATE_DIR = './config/serial'
DEPLOY_FILE = './config/deploy.yaml'

# 服务器日界（每日任务对齐到 04:00 server update）
SERVER_UPDATE_HOUR = 4
# 编排器心跳超过该时长未更新，实例视为后端不在运行（standalone 模式）
HEARTBEAT_TIMEOUT = 90

ON_ERROR_OPTIONS = ('skip', 'stop', 'retry')

_DEFAULT_STATE = {
    # 当前令牌持有者（实例名），None 表示空闲
    'current': None,
    # 当前周期（按 04:00 服务器日界划分的日期）
    'cycle': '',
    # OnError=stop 触发后置 True，停止授予令牌直到下一周期或手动重置
    'halted': False,
    # 编排器心跳（unix 时间戳）
    'heartbeat': 0,
    # 本周期出错被跳过的实例 {name: iso 时间}
    'failed': {},
    # OnError=retry 时本周期已重试过的实例 [name]
    'retried': [],
    # 各实例上报 {name: {'due_at': iso 时间, 'updated': unix 时间戳}}
    'instances': {},
}


class SerialConfig:
    def __init__(self, enable=False, group=None, on_error='skip', idle_threshold=5):
        self.enable = enable
        self.group = group or []
        self.on_error = on_error
        self.idle_threshold = idle_threshold

    def __repr__(self):
        return (
            f'SerialConfig(enable={self.enable}, group={self.group}, '
            f'on_error={self.on_error}, idle_threshold={self.idle_threshold})'
        )


def read_serial_config(file=DEPLOY_FILE):
    """
    从 deploy.yaml 读取串行配置（poor_yaml_read 会把嵌套 yaml 打平）。

    Returns:
        SerialConfig:
    """
    data = poor_yaml_read(file)
    enable = data.get('SerialEnable', False) is True
    raw_group = data.get('SerialGroup')
    # UI 以 'a > b' 有序字符串存储，兼容手写的逗号分隔
    group = [name.strip() for name in re.split(r'[>,]', str(raw_group or '')) if name.strip()]
    on_error = str(data.get('SerialOnError') or 'skip').strip()
    if on_error not in ON_ERROR_OPTIONS:
        on_error = 'skip'
    try:
        idle_threshold = int(data.get('SerialIdleThreshold', 5))
    except (TypeError, ValueError):
        idle_threshold = 5
    if idle_threshold < 1:
        idle_threshold = 1
    return SerialConfig(enable, group, on_error, idle_threshold)


def current_cycle(now=None):
    """
    按 04:00 服务器日界计算当前周期字符串。
    """
    if now is None:
        now = datetime.now()
    return (now - timedelta(hours=SERVER_UPDATE_HOUR)).date().isoformat()


def _lock():
    os.makedirs(STATE_DIR, exist_ok=True)
    return FileLock(f'{STATE_FILE}.lock')


def _default_state():
    return json.loads(json.dumps(_DEFAULT_STATE))


def get_state():
    """
    读取状态文件，缺失字段补默认值。
    """
    with _lock():
        try:
            with open(STATE_FILE, mode='r', encoding='utf-8') as f:
                state = json.load(f)
        except (OSError, ValueError):
            return _default_state()
    for key, value in _default_state().items():
        state.setdefault(key, value)
    return state


def _write_state(state):
    tmp = f'{STATE_FILE}.tmp'
    with open(tmp, mode='w', encoding='utf-8', newline='') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    # Windows 上杀软/索引可能瞬时占用目标文件导致 replace 失败，重试几次
    for attempt in range(5):
        try:
            os.replace(tmp, STATE_FILE)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def modify_state(fn):
    """
    在持锁状态下读-改-写状态文件。

    Args:
        fn (callable): fn(state) -> result，对 state 原地修改

    Returns:
        fn 的返回值
    """
    with _lock():
        try:
            with open(STATE_FILE, mode='r', encoding='utf-8') as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = _default_state()
        for key, value in _default_state().items():
            state.setdefault(key, value)
        result = fn(state)
        _write_state(state)
        return result


def report_due(name, due_at):
    """
    实例上报自己最近一个待跑任务的时间。

    Args:
        name (str): 实例名
        due_at (datetime):
    """
    def _fn(state):
        entry = state['instances'].setdefault(name, {})
        entry['due_at'] = due_at.isoformat()
        entry['updated'] = time.time()

    modify_state(_fn)


def report_waiting(name):
    """
    实例上报自己正在等待串行令牌（任务已到期但未轮到）。

    Args:
        name (str): 实例名
    """
    def _fn(state):
        entry = state['instances'].setdefault(name, {})
        entry['waiting'] = True
        entry['updated'] = time.time()

    modify_state(_fn)


def clear_waiting(name):
    """
    实例离开等待令牌状态（拿到令牌 / 串行关闭 / 配置变化 / 进程退出）。

    Args:
        name (str): 实例名
    """
    def _fn(state):
        entry = state['instances'].get(name)
        if entry and entry.get('waiting'):
            entry.pop('waiting', None)
            entry['updated'] = time.time()

    modify_state(_fn)


def get_due_at(state, name):
    """
    Returns:
        datetime | None:
    """
    raw = state['instances'].get(name, {}).get('due_at')
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def backend_alive(state=None):
    """
    编排器心跳是否新鲜。不新鲜说明后端没在跑（如 python main.py 直连），
    实例应自行放行，避免死等令牌。
    """
    if state is None:
        state = get_state()
    heartbeat = state.get('heartbeat') or 0
    return time.time() - heartbeat < HEARTBEAT_TIMEOUT


def is_my_turn(name):
    """
    实例侧闸门判断：是否轮到自己操作设备。
    后端不在运行（standalone）时一律放行。
    """
    state = get_state()
    if not backend_alive(state):
        return True
    return state.get('current') == name
