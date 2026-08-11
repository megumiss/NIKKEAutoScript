import argparse
import mimetypes
import sys
import threading
from typing import Any, Dict, List

# On Windows, mimetypes reads Content-Type from the registry (HKCR\.js),
# which may be hijacked to "text/plain" by third-party software.
# Browsers refuse to execute module scripts with a non-JS MIME type,
# resulting in a blank page. Force correct types here.
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('text/javascript', '.mjs')
mimetypes.add_type('text/css', '.css')

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import module.webui.lang as lang
from module.config.env import IS_ON_PHONE_CLOUD
from module.logger import logger
from module.webui.process_manager import ProcessManager
from module.webui.remote_access import RemoteAccess
from module.webui.setting import State
from module.webui.updater import updater
from module.webui.utils import TaskHandler

task_handler = TaskHandler()


class HeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _current_is_zh() -> bool:
    return str(lang.LANG or '').lower().startswith('zh')


def _normalize_option_value(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _get_monitor_orientation_label(orientation: int) -> str:
    if _current_is_zh():
        mapping = {
            0: '横向',
            1: '纵向',
            2: '横向(翻转)',
            3: '纵向(翻转)',
        }
    else:
        mapping = {
            0: 'Landscape',
            1: 'Portrait',
            2: 'Landscape (Flipped)',
            3: 'Portrait (Flipped)',
        }
    return mapping.get(orientation, str(orientation))


def _query_windows_monitors() -> List[Dict[str, Any]]:
    if not sys.platform.startswith('win'):
        return []

    try:
        import ctypes
        from ctypes import wintypes
        import win32api
        import win32con
    except Exception as e:
        logger.warning(f'Failed to import monitor APIs: {e}')
        return []

    get_scale_factor_for_monitor = None
    get_dpi_for_monitor = None
    mdt_effective_dpi = 0
    try:
        shcore = ctypes.windll.shcore
        get_scale_factor_for_monitor = shcore.GetScaleFactorForMonitor
        get_scale_factor_for_monitor.argtypes = [wintypes.HMONITOR, ctypes.POINTER(ctypes.c_uint)]
        get_scale_factor_for_monitor.restype = ctypes.c_long

        get_dpi_for_monitor = shcore.GetDpiForMonitor
        get_dpi_for_monitor.argtypes = [
            wintypes.HMONITOR,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        get_dpi_for_monitor.restype = ctypes.c_long
    except Exception:
        pass

    monitors: List[Dict[str, Any]] = []
    try:
        for idx, (monitor_handle, _, monitor_rect) in enumerate(win32api.EnumDisplayMonitors()):
            info = win32api.GetMonitorInfo(monitor_handle)
            monitor = info.get('Monitor') or monitor_rect
            device_name = str(info.get('Device', f'DISPLAY{idx + 1}'))

            width = int(monitor[2] - monitor[0])
            height = int(monitor[3] - monitor[1])

            refresh_rate = 0
            orientation = 0
            physical_width = width
            physical_height = height
            try:
                dm = win32api.EnumDisplaySettings(device_name, win32con.ENUM_CURRENT_SETTINGS)
                refresh_rate = int(getattr(dm, 'DisplayFrequency', 0) or 0)
                orientation = int(getattr(dm, 'DisplayOrientation', 0) or 0)
                physical_width = int(getattr(dm, 'PelsWidth', 0) or physical_width)
                physical_height = int(getattr(dm, 'PelsHeight', 0) or physical_height)
            except Exception:
                pass

            scale_percent = 0
            scale_known = False
            dpi_x = 0
            monitor_ptr = wintypes.HMONITOR(int(monitor_handle))
            if get_scale_factor_for_monitor is not None:
                scale_holder = ctypes.c_uint(0)
                if get_scale_factor_for_monitor(monitor_ptr, ctypes.byref(scale_holder)) == 0:
                    if scale_holder.value:
                        scale_percent = int(scale_holder.value)
                        scale_known = True

            if get_dpi_for_monitor is not None:
                dpi_x_holder = ctypes.c_uint(0)
                dpi_y_holder = ctypes.c_uint(0)
                if get_dpi_for_monitor(
                    monitor_ptr,
                    mdt_effective_dpi,
                    ctypes.byref(dpi_x_holder),
                    ctypes.byref(dpi_y_holder),
                ) == 0:
                    dpi_x = int(dpi_x_holder.value or 0)
                    if dpi_x and not scale_known:
                        scale_percent = int(round(dpi_x * 100 / 96))
                        scale_known = scale_percent > 0

            if not scale_known:
                # Fallback: derive scale from physical/logical resolution when possible.
                inferred_scales = []
                if width > 0 and physical_width > 0:
                    inferred_scales.append(int(round(physical_width * 100 / width)))
                if height > 0 and physical_height > 0:
                    inferred_scales.append(int(round(physical_height * 100 / height)))
                if inferred_scales:
                    scale_percent = max(100, int(round(sum(inferred_scales) / len(inferred_scales))))
                else:
                    scale_percent = 100

            monitors.append(
                {
                    'index': idx,
                    'refresh_rate': refresh_rate,
                    'orientation_label': _get_monitor_orientation_label(orientation),
                    'physical_width': physical_width,
                    'physical_height': physical_height,
                    'scale_percent': scale_percent,
                }
            )
    except Exception as e:
        logger.warning(f'Failed to enumerate monitors: {e}')

    return monitors


def _build_screen_number_options(current_value: Any = None) -> List[Dict[str, Any]]:
    monitors = _query_windows_monitors()
    is_zh = _current_is_zh()

    options: List[Dict[str, Any]] = []
    for monitor in monitors:
        idx = int(monitor['index'])
        unknown_text = '未知'
        if not is_zh:
            unknown_text = 'N/A'
        refresh_text = f"{monitor['refresh_rate']}Hz" if monitor['refresh_rate'] else unknown_text
        physical_resolution = f"{monitor['physical_width']}x{monitor['physical_height']}"
        resolution_text = f"{physical_resolution} @{monitor['scale_percent']}%"
        label = (
            f"{idx} | {resolution_text} | {refresh_text} | "
            f"{monitor['orientation_label']}"
        )
        options.append(
            {
                'value': idx,
                'label': label,
            }
        )

    if not options:
        for i in range(6):
            options.append({'value': i, 'label': str(i)})

    normalized_value = _normalize_option_value(current_value)
    if normalized_value is not None and normalized_value not in [opt['value'] for opt in options]:
        if is_zh:
            extra_label = f'{normalized_value} | 当前配置(未检测到对应显示器)'
        else:
            extra_label = f'{normalized_value} | Current setting (monitor not detected)'
        options.append({'value': normalized_value, 'label': extra_label})

    return options


def startup():
    State.init()
    lang.reload()
    from module.warehouse_stats.data import init_warehouse_stats_files, preload_warehouse_assets
    init_warehouse_stats_files(preload_assets=False)
    # 必须在 fork 实例进程之前完成资产预载：若在后台线程预载（import scipy
    # 等大模块）期间 fork，子进程会继承「进行中」的 import 锁，导致实例进程
    # 同样 import 这些模块时死锁，表现为更新重启后实例卡在 [Script Path]
    # （Docker/Linux fork 环境，Windows spawn 不受影响）。同步预载约 0.5s。
    preload_warehouse_assets()
    updater.event = State.manager.Event()
    if updater.delay > 0:
        task_handler.add(updater.check_update, updater.delay)
    task_handler.add(updater.schedule_update(), 86400)
    task_handler.start()
    # 多实例顺序执行编排器（SerialEnable 开启时生效）
    from module.webui.serial_orchestrator import serial_orchestrator
    serial_orchestrator.start(ev=updater.event)
    # if State.deploy_config.DiscordRichPresence:
    #     init_discord_rpc()
    # if State.deploy_config.StartOcrServer:
    #     start_ocr_server_process(State.deploy_config.OcrServerPort)
    if (
        State.deploy_config.EnableRemoteAccess
        and State.deploy_config.Password is not None
    ):
        task_handler.add(RemoteAccess.keep_ssh_alive(), 60)


def clearup():
    """
    Notice: Ensure run it before uvicorn reload app,
    all process will NOT EXIT after close electron app.
    """
    logger.info("Start clearup")
    from module.webui.serial_orchestrator import serial_orchestrator
    serial_orchestrator.stop()
    RemoteAccess.kill_ssh_process()
    # close_discord_rpc()
    # stop_ocr_server_process()
    for nkas in ProcessManager._processes.values():
        nkas.stop()
    State.clearup()
    task_handler.stop()
    logger.info("NKAS closed.")


def app():
    parser = argparse.ArgumentParser(description="NKAS web service")
    parser.add_argument(
        "--run",
        nargs="+",
        type=str,
        help="Run nkas by config names on startup",
    )
    args, _ = parser.parse_known_args()

    # Apply config
    State.theme = State.deploy_config.Theme
    lang.LANG = State.deploy_config.Language
    runs = None
    if args.run:
        runs = args.run
    elif State.deploy_config.Run:
        # TODO: refactor poor_yaml_read() to support list
        tmp = State.deploy_config.Run.split(",")
        runs = [l.strip(" ['\"]") for l in tmp if len(l)]
    instances: List[str] = runs

    logger.hr("Webui configs")
    logger.attr("Theme", State.deploy_config.Theme)
    logger.attr("Language", lang.LANG)
    logger.attr("IS_ON_PHONE_CLOUD", IS_ON_PHONE_CLOUD)

    from deploy.atomic import atomic_failure_cleanup
    atomic_failure_cleanup('./config')

    async def spa_index(request):
        """Published entry point for browsers and legacy Electron iframe shells."""
        return RedirectResponse('/app/', status_code=302)

    app = Starlette(
        routes=[
            Route('/', spa_index, methods=['GET']),
            Mount('/static', app=StaticFiles(directory='./assets'), name='static'),
        ],
        middleware=[Middleware(HeaderMiddleware)],
        debug=True,
        on_startup=[
            startup,
            lambda: ProcessManager.restart_processes(
                instances=instances, ev=updater.event
            ),
        ],
        on_shutdown=[clearup],
    )

    from module.webui.api import mount_api
    mount_api(app)

    return app
