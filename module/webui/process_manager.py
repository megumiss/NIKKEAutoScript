import argparse
import json
import logging
import os
import queue
import subprocess
import threading
import time
from multiprocessing import Process
from typing import Dict, List, Tuple, Union

import inflection
import psutil
from rich.console import Console, ConsoleRenderable

from module.base.utils import set_preview_queue
from module.submodule.utils import get_available_func, get_available_mod, get_available_mod_func, get_config_mod, \
    get_func_mod, list_mod_instance, load_mod
from module.logger import logger, set_file_logger, set_func_logger
from module.webui.setting import State


class ProcessManager:
    _processes: Dict[str, "ProcessManager"] = {}
    # get_manager 的 check-then-act 必须加锁：后端启动时编排器线程与
    # restart_processes 会并发调用，否则同一实例名可能创建出两个 manager，
    # 后写入的空对象覆盖真正 fork 了实例进程的对象（表现为后端误判实例已停止）。
    _processes_lock = threading.Lock()

    def __init__(self, config_name: str = "nkas") -> None:
        self.config_name = config_name
        self._renderable_queue: queue.Queue[Tuple[int, ConsoleRenderable]] = State.manager.Queue()
        # Replay buffer entries are (levelno, renderable).  Retention is per
        # level class so a debug flood cannot push info/warn/error lines out
        # of the replay: the SPA filter defaults to info and would otherwise
        # show only a handful of rows after a page load or reconnect.
        self.renderables: List[Tuple[int, ConsoleRenderable]] = []
        self.renderables_max_length = 400
        self.renderables_max_total = self.renderables_max_length * 2
        # WebSocket consumers receive an independent bounded queue.  Keeping
        # these queues out of the logging thread's critical path preserves the
        # existing RichLog behaviour even when a browser is slow or closed.
        self._log_subscribers: List[queue.Queue] = []
        self._log_subscribers_lock = threading.Lock()
        # Preview frames published by the worker (JPEG bytes). The web process
        # drains this queue on a thread and keeps only the newest frame in
        # memory; nothing touches disk.
        self._preview_queue: queue.Queue = State.manager.Queue(maxsize=2)
        self.latest_preview: Tuple[float, bytes] = None
        self._process: Process = None
        self._process_locks: Dict[str, threading.Lock] = {}
        self.thd_log_queue_handler: threading.Thread = None
        self.thd_preview_handler: threading.Thread = None

    def start(self, func, ev: threading.Event = None) -> None:
        if not self.alive:
            if func is None:
                func = get_config_mod(self.config_name)
            self._process = Process(
                target=ProcessManager.run_process,
                args=(
                    self.config_name,
                    func,
                    self._renderable_queue,
                    self._preview_queue,
                    ev,
                ),
            )
            self._process.start()
            self.start_log_queue_handler()
            self.start_preview_handler()

    def start_log_queue_handler(self):
        if (
            self.thd_log_queue_handler is not None
            and self.thd_log_queue_handler.is_alive()
        ):
            return
        self.thd_log_queue_handler = threading.Thread(
            target=self._thread_log_queue_handler
        )
        self.thd_log_queue_handler.start()

    def start_preview_handler(self):
        if (
            self.thd_preview_handler is not None
            and self.thd_preview_handler.is_alive()
        ):
            return
        self.thd_preview_handler = threading.Thread(
            target=self._thread_preview_handler
        )
        self.thd_preview_handler.daemon = True
        self.thd_preview_handler.start()

    def _thread_preview_handler(self) -> None:
        while self.alive:
            try:
                item = self._preview_queue.get(timeout=1)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                # Worker died and the queue pipe broke; keep the last frame.
                break
            if isinstance(item, bytes):
                self.latest_preview = (time.time(), item)

    def stop(self) -> None:
        try:
            lock = self._process_locks[self.config_name]
        except KeyError:
            lock = threading.Lock()
            self._process_locks[self.config_name] = lock

        with lock:
            if self.alive:
                self._terminate_worker_children()
                self._process.kill()
                self.renderables.append(
                    (logging.INFO, f"[{self.config_name}] exited. Reason: Manual stop\n")
                )
                self._run_stop_cleanup()
            if self.thd_log_queue_handler is not None:
                self.thd_log_queue_handler.join(timeout=1)
                if self.thd_log_queue_handler.is_alive():
                    logger.warning(
                        "Log queue handler thread does not stop within 1 seconds"
                    )
        logger.info(f"[{self.config_name}] exited")

    def _terminate_worker_children(self) -> None:
        try:
            children = psutil.Process(self._process.pid).children(recursive=True)
        except (psutil.Error, OSError):
            return
        # win 平台：游戏与启动器由实例进程 Popen 拉起，属于其子进程树，不能连带终止
        protected = self._win_protected_process_names()
        if protected:
            kept = []
            for child in children:
                try:
                    if child.name().lower() in protected:
                        continue
                except psutil.Error:
                    pass
                kept.append(child)
            children = kept
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(children, timeout=3)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass

    def _win_protected_process_names(self):
        """
        win 平台手动停止时需要保护的进程名（游戏本体与启动器，小写）。
        非 win 平台或读取失败时返回空集。
        直接读配置 JSON，避免在 webui 进程里 import win 设备模块带来的副作用。
        """
        from module.config.utils import filepath_config

        try:
            with open(filepath_config(self.config_name), encoding='utf-8') as f:
                data = json.load(f)
            if data.get('NKAS', {}).get('Client', {}).get('Platform') != 'win':
                return set()
            info = data.get('PCClient', {}).get('PCClientInfo', {})
            client = info.get('Client', 'intl')
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f'[{self.config_name}] Failed to read PC client config: {e}')
            return set()
        # 默认值与 module/device/win/app_control.py 的 GAME_/LAUNCHER_PROCESS 保持一致
        defaults = {
            'intl': ('nikke.exe', 'nikke_launcher.exe'),
            'hmt': ('nikke.exe', 'nikke_launcher_hmt.exe'),
        }
        default_game, default_launcher = defaults.get(client, defaults['intl'])
        return {
            str(info.get('GameProcessName') or default_game).lower(),
            str(info.get('LauncherProcessName') or default_launcher).lower(),
        }

    def _physical_device_context(self):
        """
        读取真机配置，返回 (physical, serial)。
        非真机模式、serial 无效或读取失败时返回 (None, None)。
        直接读配置 JSON，避免在 webui 进程里实例化 NikkeConfig 带来的副作用。
        """
        from module.config.utils import filepath_config

        try:
            with open(filepath_config(self.config_name), encoding='utf-8') as f:
                data = json.load(f)
            emulator = data.get('Emulator', {})
            physical = emulator.get('PhysicalDevice', {})
            if not physical.get('Enable', False):
                return None, None
            serial = str(emulator.get('Emulator', {}).get('Serial', ''))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f'[{self.config_name}] Failed to read physical device config: {e}')
            return None, None
        if not serial or serial == 'auto':
            return None, None
        return physical, serial

    @staticmethod
    def _find_adb() -> str:
        adb = State.deploy_config.AdbExecutable.replace('\\', '/')
        if not os.path.exists(adb):
            adb = next((f for f in [
                './bin/adb/adb.exe',
                './toolkit/Lib/site-packages/adbutils/binaries/adb.exe',
                '/usr/bin/adb',
            ] if os.path.exists(f)), 'adb')
        return adb

    def _restore_physical_device_resolution(self) -> None:
        """
        真机模式停止实例时恢复设备分辨率（wm size reset）。
        实例进程被 kill 时其 atexit 不会执行，所以由 webui 进程兜底。
        """
        physical, serial = self._physical_device_context()
        if not physical:
            return
        if physical.get('VirtualDisplay', False):
            return
        if not physical.get('AutoRestoreResolution', True):
            return

        adb = self._find_adb()

        try:
            result = subprocess.run(
                [adb, '-s', serial, 'shell', 'wm', 'size', 'reset'],
                timeout=10, capture_output=True,
            )
            result2 = subprocess.run(
                [adb, '-s', serial, 'shell', 'wm', 'density', 'reset'],
                timeout=10, capture_output=True,
            )
            if result.returncode == 0 and result2.returncode == 0:
                self.renderables.append(
                    (logging.INFO, f"[{self.config_name}] Physical device resolution restored (wm size reset)\n")
                )
            else:
                logger.warning(
                    f'[{self.config_name}] wm size reset failed: {result.stderr!r}, '
                    f'run `adb -s {serial} shell wm size reset` manually'
                )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(
                f'[{self.config_name}] Failed to restore resolution: {e}, '
                f'run `adb -s {serial} shell wm size reset` manually'
            )

    def _cleanup_virtual_display_server(self) -> None:
        """
        虚拟屏幕模式停止实例时清理设备端残留的 nkas-vd-server。
        实例进程被 kill 时其 atexit 不会执行，本地 adb 桥被杀后设备端 app_process 可能存活，
        继续持有虚拟屏幕和游戏画面；SIGTERM 会触发其 shutdown hook 正常释放虚拟屏幕。
        worker 正常退出时设备端已被 atexit 清理，pkill 无匹配返回 1，属正常路径。
        """
        physical, serial = self._physical_device_context()
        if not physical or not physical.get('VirtualDisplay', False):
            return

        adb = self._find_adb()

        try:
            result = subprocess.run(
                [adb, '-s', serial, 'shell', 'pkill', '-f', 'com.nkas.virtualdisplay.Server'],
                timeout=10, capture_output=True,
            )
            if result.returncode == 0:
                self.renderables.append(
                    (logging.INFO, f"[{self.config_name}] Virtual display server cleaned up on device\n")
                )
            elif result.returncode != 1:
                logger.warning(
                    f'[{self.config_name}] pkill virtual display server failed: {result.stderr!r}, '
                    f'run `adb -s {serial} shell pkill -f com.nkas.virtualdisplay.Server` manually'
                )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(
                f'[{self.config_name}] Failed to clean up virtual display server: {e}, '
                f'run `adb -s {serial} shell pkill -f com.nkas.virtualdisplay.Server` manually'
            )

    def _run_stop_cleanup(self) -> None:
        """手动停止时实例进程被直接 kill，其 _post_action/atexit 不会执行，
        由 GUI 进程代为清理：真机实例还原分辨率、清理设备端 vd-server；
        win 实例还原屏幕方向、禁用 VDD 虚拟屏。
        游戏声音恢复依赖游戏窗口句柄，不在此处处理。"""
        # 真机清理只读配置 JSON，不实例化 NikkeConfig，避免 win 提前返回把它跳过
        self._restore_physical_device_resolution()
        self._cleanup_virtual_display_server()
        try:
            from module.config.config import NikkeConfig
            config = NikkeConfig(self.config_name, task=None)
        except Exception as e:
            logger.warning(f'Failed to load config for stop cleanup: {e}')
            return
        if config.Client_Platform != 'win':
            return
        if config.PCClient_ScreenRotate:
            try:
                from module.device.win.game_control import WinClient
                WinClient.screen_rotate(config.PCClient_ScreenNumber)
            except Exception as e:
                logger.warning(f'Failed to restore screen orientation on stop: {e}')
        if config.PCClient_VddScreen and config.PCClient_VddAutoManage:
            try:
                from module.device.win.vdd import vdd_auto_stop
                vdd_auto_stop()
            except Exception as e:
                logger.warning(f'Failed to disable VDD screen on stop: {e}')

    def _thread_log_queue_handler(self) -> None:
        while self.alive:
            try:
                item = self._renderable_queue.get(timeout=1)
            except queue.Empty:
                continue
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], int):
                levelno, log = item
            else:
                # Legacy producers put bare renderables; treat them as
                # non-debug so a debug flood cannot evict them.
                levelno, log = logging.INFO, item
            self.renderables.append((levelno, log))
            self._trim_renderables()
            self._publish_log(log)
        logger.info("End of log queue handler loop")

    def _trim_renderables(self) -> None:
        # Evict the oldest entry of whichever level class is over its limit;
        # debug and non-debug lines each keep the last renderables_max_length
        # entries.  The SPA live buffer applies the same rule.
        if len(self.renderables) <= self.renderables_max_total:
            return
        debug_count = sum(1 for levelno, _ in self.renderables if levelno <= logging.DEBUG)
        while len(self.renderables) > self.renderables_max_total:
            evict_debug = debug_count > self.renderables_max_length
            for index, (levelno, _) in enumerate(self.renderables):
                if (levelno <= logging.DEBUG) == evict_debug:
                    del self.renderables[index]
                    if evict_debug:
                        debug_count -= 1
                    break
            else:
                break

    def replay_renderables(self) -> List[ConsoleRenderable]:
        return [log for _, log in self.renderables]

    def subscribe_log(self) -> queue.Queue:
        """Return a bounded queue which receives new rendered log entries."""
        subscriber = queue.Queue(maxsize=self.renderables_max_length)
        with self._log_subscribers_lock:
            self._log_subscribers.append(subscriber)
        return subscriber

    def unsubscribe_log(self, subscriber: queue.Queue) -> None:
        with self._log_subscribers_lock:
            try:
                self._log_subscribers.remove(subscriber)
            except ValueError:
                pass

    def _publish_log(self, log: ConsoleRenderable) -> None:
        with self._log_subscribers_lock:
            subscribers = list(self._log_subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(log)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(log)
                except queue.Full:
                    # A concurrent consumer may have filled it again; dropping
                    # one incremental line is preferable to blocking logs.
                    pass

    @property
    def alive(self) -> bool:
        if self._process is not None:
            return self._process.is_alive()
        else:
            return False

    @property
    def state(self) -> int:
        if self.alive:
            return 1
        elif len(self.renderables) == 0:
            return 2
        else:
            console = Console(no_color=True)
            with console.capture() as capture:
                console.print(self.renderables[-1][1])
            s = capture.get().strip()
            if s.endswith("Reason: Manual stop"):
                return 2
            elif s.endswith("Reason: Finish"):
                return 2
            elif s.endswith("Reason: Update"):
                return 4
            else:
                return 3

    @classmethod
    def get_manager(cls, config_name: str) -> "ProcessManager":
        """
        Create a new nkas if not exists.
        """
        with cls._processes_lock:
            if config_name not in cls._processes:
                cls._processes[config_name] = ProcessManager(config_name)
            return cls._processes[config_name]

    @classmethod
    def rename_process(cls, name: str, new_name: str) -> None:
        """Re-key an existing manager after an instance rename; no-op when no
        manager exists for the old name (the instance was never started)."""
        with cls._processes_lock:
            manager = cls._processes.pop(name, None)
            if manager is not None:
                manager.config_name = new_name
                cls._processes[new_name] = manager

    @staticmethod
    def run_process(
        config_name, func: str, q: queue.Queue, pq: queue.Queue, e: threading.Event = None
    ) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--electron", action="store_true", help="Runs by electron client."
        )
        args, _ = parser.parse_known_args()
        State.electron = args.electron

        # Setup logger
        set_file_logger(name=config_name)
        if State.electron:
            # https://github.com/LmeSzinc/AzurLaneAutoScript/issues/2051
            logger.info("Electron detected, remove log output to stdout")
            from module.logger import console_hdlr
            logger.removeHandler(console_hdlr)
        set_func_logger(func=q.put)
        set_preview_queue(pq)

        from module.config.config import NikkeConfig

        NikkeConfig.stop_event = e
        try:
            # Run nkas
            if func == "nkas":
                from main import NikkeAutoScript

                if e is not None:
                    NikkeAutoScript.stop_event = e
                NikkeAutoScript(config_name=config_name).loop()
            elif func in get_available_func():
                from main import NikkeAutoScript

                NikkeAutoScript(config_name=config_name).run(inflection.underscore(func), skip_first_screenshot=True)
            elif func in get_available_mod():
                mod = load_mod(func)

                if e is not None:
                    mod.set_stop_event(e)
                mod.loop(config_name)
            elif func in get_available_mod_func():
                getattr(load_mod(get_func_mod(func)), inflection.underscore(func))(config_name)
            else:
                logger.critical(f"No function matched: {func}")
            logger.info(f"[{config_name}] exited. Reason: Finish\n")
        except Exception as e:
            logger.exception(e)

    @classmethod
    def running_instances(cls) -> List["ProcessManager"]:
        l = []
        for process in cls._processes.values():
            if process.alive:
                l.append(process)
        return l

    @staticmethod
    def restart_processes(
        instances: List[Union["ProcessManager", str]] = None, ev: threading.Event = None
    ):
        """
        After update and reload, or failed to perform an update,
        restart all nkas that running before update
        """
        logger.hr("Restart nkas")

        # Load MOD_CONFIG_DICT
        list_mod_instance()

        if instances is None:
            instances = []

        _instances = set()

        for instance in instances:
            if isinstance(instance, str):
                _instances.add(ProcessManager.get_manager(instance))
            elif isinstance(instance, ProcessManager):
                _instances.add(instance)

        try:
            with open("./config/reloadnkas", mode="r") as f:
                for line in f.readlines():
                    line = line.strip()
                    _instances.add(ProcessManager.get_manager(line))
        except FileNotFoundError:
            pass

        for process in _instances:
            logger.info(f"Starting [{process.config_name}]")
            process.start(func=get_config_mod(process.config_name), ev=ev)

        try:
            os.remove("./config/reloadnkas")
        except:
            pass
        logger.info("Start nkas complete")
