"""
串行编排器：在后端进程内以守护线程运行。

按 deploy.yaml 的 Serial 配置（SerialEnable / SerialGroup / SerialOnError /
SerialIdleThreshold）为组内实例分配"执行令牌"，保证同一时刻只有一个实例
操作设备（win 平台的全局鼠标/前台焦点是独占资源）。

工作方式：
- 实例进程保持常驻，调度循环内自建闸门（main.py serial_wait_turn），
  未持令牌时任务到期也只等待，不碰设备。
- 编排器每 5 秒一轮：持有者空闲（最近任务距今超过阈值）/ 出错 / 被停止
  时释放令牌，然后按组内顺序授予第一个"有任务到期"的存活实例。
- 每日 04:00（服务器日界）重置周期：清空出错记录与停止标记。
- 状态持久化在 ./config/serial/state.json，后端重启/每日自动更新后不丢。
"""

import threading
import time
from datetime import datetime, timedelta

from module.config.serial_state import (
    current_cycle,
    get_due_at,
    get_state,
    modify_state,
    read_serial_config,
)
from module.logger import logger
from module.webui.process_manager import ProcessManager

# 授予令牌后的宽限期（秒）：实例进程可能还没来得及上报 due_at，
# 宽限期内不做空闲释放判断，避免令牌刚授予就被回收。
GRANT_GRACE = 120
# 判定"任务到期"时的向前宽容（秒），吸收实例上报与编排器轮询的时间差
DUE_GRACE = 30


class SerialOrchestrator:
    def __init__(self):
        self._thread = None
        self._alive = False
        self._event = None

    def start(self, ev=None):
        """
        Args:
            ev: updater 的 stop_event，重试拉起实例时透传给子进程。
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._event = ev
        self._alive = True
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self):
        self._alive = False
        if self._thread is not None:
            self._thread.join(timeout=2)

    def run(self):
        logger.info('Serial orchestrator started')
        while self._alive:
            try:
                self.tick()
            except Exception as e:
                logger.exception(e)
            time.sleep(5)

    def tick(self):
        config = read_serial_config()
        if not config.enable or not config.group:
            return
        now = datetime.now()

        # 心跳：实例据此判断后端是否在运行（standalone 模式自行放行）
        def _heartbeat(s):
            s['heartbeat'] = time.time()
        modify_state(_heartbeat)

        # 每日周期重置（04:00 服务器日界）
        cycle = current_cycle(now)
        state = get_state()
        if state['cycle'] != cycle:
            logger.info(f'Serial: new cycle {cycle}, reset failed/halted')
            def _reset(s):
                s['cycle'] = cycle
                s['failed'] = {}
                s['retried'] = []
                s['halted'] = False
            modify_state(_reset)
            state = get_state()

        current = state.get('current')

        # 1. 持有者检查：出错 / 停止 / 空闲时释放令牌
        if current:
            if current not in config.group:
                self._release(current, reason='not in serial group')
                current = None
            else:
                manager = ProcessManager.get_manager(current)
                state_code = manager.state
                if state_code == 3:
                    # 出错退出（state 3）：按 OnError 处理
                    self._mark_failed(current)
                    self._release(current, reason='error')
                    if config.on_error == 'stop':
                        self._set_halted()
                        logger.warning(
                            f'Serial: [{current}] stopped with error, halt serial run (OnError=stop)'
                        )
                    else:
                        logger.warning(
                            f'Serial: [{current}] stopped with error, skip (OnError={config.on_error})'
                        )
                    current = None
                elif not manager.alive:
                    # 手动停止或更新重启（state 2/4）：释放令牌，不记出错
                    self._release(current, reason='process not running')
                    current = None
                else:
                    due = get_due_at(state, current)
                    granted_at = state.get('granted_at') or 0
                    if due is not None and time.time() - granted_at > GRANT_GRACE:
                        if due > now + timedelta(minutes=config.idle_threshold):
                            self._release(current, reason=f'idle until {due}')
                            current = None

        # 2. 授予令牌：组内顺序，第一个有任务到期的存活实例
        state = get_state()
        if current is None and not state.get('halted'):
            granted = self._grant_next(config, state, now)
            # 3. OnError=retry：无事可做时，重启一个本周期出错的实例
            if not granted and config.on_error == 'retry':
                self._retry_failed(config, state)

    def _grant_next(self, config, state, now):
        for name in config.group:
            if name in state['failed']:
                continue
            manager = ProcessManager.get_manager(name)
            if not manager.alive:
                # 不自动拉起停止的实例，避免与用户手动 stop 对抗
                continue
            due = get_due_at(state, name)
            if due is None:
                # 实例刚启动还未上报，下一轮再说
                continue
            if due <= now + timedelta(seconds=DUE_GRACE):
                self._grant(name)
                return True
        return False

    def _retry_failed(self, config, state):
        for name in config.group:
            if name not in state['failed'] or name in state['retried']:
                continue
            manager = ProcessManager.get_manager(name)
            if manager.alive:
                continue
            logger.warning(f'Serial: retry errored instance [{name}]')
            try:
                from module.submodule.utils import get_config_mod
                manager.start(func=get_config_mod(name), ev=self._event)
            except Exception as e:
                logger.exception(e)
                continue

            def _fn(s):
                s['failed'].pop(name, None)
                if name not in s['retried']:
                    s['retried'].append(name)
            modify_state(_fn)
            return True
        return False

    def _grant(self, name):
        logger.info(f'Serial: grant turn to [{name}]')

        def _fn(s):
            s['current'] = name
            s['granted_at'] = time.time()
        modify_state(_fn)

    def _release(self, name, reason=''):
        logger.info(f'Serial: release turn from [{name}], reason: {reason}')

        def _fn(s):
            if s.get('current') == name:
                s['current'] = None
        modify_state(_fn)

    def _mark_failed(self, name):
        def _fn(s):
            s['failed'][name] = datetime.now().isoformat()
        modify_state(_fn)

    def _set_halted(self):
        def _fn(s):
            s['halted'] = True
        modify_state(_fn)


serial_orchestrator = SerialOrchestrator()
