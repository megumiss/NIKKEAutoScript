"""
ParsecVDD 虚拟屏管理。

与 MttVDD（见 `vdd.py`）不同，ParsecVDD 由官方驱动 + 本仓库内置的
`bin/parsec_vdd/ParsecDisplay.exe` 管理：

- 开 = 启动 `ParsecDisplay.exe -silent`，该进程按注册表快照自动重建虚拟屏；
- 关 = 结束该进程，驱动约 1 秒后自动移除所有虚拟屏。

因此不需要管理员权限（MttVDD 需要 Enable-PnpDevice）。NKAS 只依赖
「官方驱动已安装」，不依赖 ParsecDisplay 的安装路径。

CLI 调用约定（`-cli` 前缀即上游 vdd 命令）：
- 退出码不可靠：`list` 在「有屏幕」时返回 1、「无屏幕」时返回 0，
  所以一律解析 stdout，不判断退出码；
- 输出文本使用系统 ANSI 代码页（中文系统为 GBK），解码见 `_decode`。
"""

import os
import re
import subprocess
import time

import psutil

from module.device.win.vdd import VddError
from module.logger import logger

PARSEC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '../../../bin/parsec_vdd'))
PARSEC_EXE = os.path.join(PARSEC_DIR, 'ParsecDisplay.exe')
PROCESS_NAME = 'ParsecDisplay.exe'

# 官方驱动在设备管理器中的硬件 ID
DRIVER_HARDWARE_ID = r'Root\Parsec\VDA'

# 目标模式：1080p 竖屏
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_HZ = 60
TARGET_ORIENTATION = 1  # DMDO_90，竖屏

WAIT_DISPLAY_TIMEOUT = 15


class ParsecVddError(VddError):
    """ParsecVDD 相关错误。继承 VddError，使既有的 except VddError 兜底仍然生效。"""


def _decode(data: bytes) -> str:
    """
    CLI 输出按系统 ANSI 代码页编码（中文系统为 GBK，如 '×' 为 0xA1 0xC1）。

    解析只依赖 ASCII 字段，编码不匹配时退化为乱码也不影响正则匹配。
    """
    for encoding in ('mbcs', 'utf-8', 'gbk'):
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode('latin-1', errors='replace')


def _cli(*args, timeout=30) -> str:
    """
    执行 `ParsecDisplay.exe -cli <args>` 并返回 stdout 文本。

    不按退出码判断成败（见模块 docstring），由调用方解析输出。
    """
    if os.name != 'nt':
        raise ParsecVddError('ParsecVDD is only supported on Windows')
    if not os.path.isfile(PARSEC_EXE):
        raise ParsecVddError(f'ParsecDisplay.exe not found: {PARSEC_EXE}')
    try:
        result = subprocess.run([PARSEC_EXE, '-cli', *args], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ParsecVddError(f'ParsecDisplay -cli {" ".join(args)} timed out after {timeout}s')
    except OSError as e:
        raise ParsecVddError(f'Failed to run ParsecDisplay -cli {" ".join(args)}: {e}')
    return _decode(result.stdout)


def _search(pattern, text, flags=0):
    match = re.search(pattern, text, flags)
    return match.group(1) if match else None


def _pnp_installed(timeout=20):
    """
    用 Get-PnpDevice 兜底判断驱动是否安装，用于区分「驱动未装」与「CLI 异常」。

    Returns:
        bool | None: None 表示查询本身失败，无法判断
    """
    script = (
        f"Get-PnpDevice -HardwareID '{DRIVER_HARDWARE_ID}' -ErrorAction SilentlyContinue "
        f'| Select-Object -First 1 -ExpandProperty Status'
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug(f'Get-PnpDevice check failed: {e}')
        return None
    return bool(result.stdout.strip())


def driver_status() -> dict:
    """
    查询官方 Parsec VDD 驱动状态。

    优先用 `-cli version`（驱动自报状态，无额外进程开销），
    无法识别输出时再用 Get-PnpDevice 兜底。

    Returns:
        dict: {'installed': bool, 'status': str, 'version': str, 'message': str}
    """
    try:
        output = _cli('version')
    except ParsecVddError as e:
        output = ''
        error = str(e)
    else:
        error = ''

    status = _search(r'-\s*Status:\s*(\S+)', output)
    version = _search(r'-\s*Version:\s*(\S+)', output)
    if status is not None:
        installed = status.upper() == 'OK'
        return {
            'installed': installed,
            'status': status,
            'version': version or '',
            'message': '' if installed else f'Parsec VDD driver status is {status}',
        }

    # `-cli version` 无有效输出，用 PnP 兜底判断是否装了驱动
    if _pnp_installed():
        return {
            'installed': True,
            'status': 'unknown',
            'version': version or '',
            'message': 'Parsec VDD driver detected, but `ParsecDisplay -cli version` returned no status',
        }
    return {
        'installed': False,
        'status': 'not_found',
        'version': '',
        'message': error or f'Parsec VDD driver not found (HardwareID {DRIVER_HARDWARE_ID})',
    }


def _iter_app_processes():
    """
    返回当前运行的 ParsecDisplay 进程列表 [(psutil.Process, exe_path|None)]。

    按进程名匹配而非路径：驱动只允许一个实例，屏幕状态是全局的，
    用户手动从其它目录启动的实例同样需要被 stop_app() 关掉。
    """
    found = []
    for proc in psutil.process_iter(attrs=['pid', 'name']):
        try:
            if (proc.info['name'] or '').lower() != PROCESS_NAME.lower():
                continue
            handle = psutil.Process(proc.info['pid'])
            try:
                path = handle.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                path = None
            found.append((handle, path))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def is_app_running() -> bool:
    """ParsecDisplay 常驻进程是否在运行"""
    return bool(_iter_app_processes())


def start_app():
    """启动 `ParsecDisplay.exe -silent` 常驻进程（自带 RestoreDisplays，按注册表快照重建屏幕）"""
    if is_app_running():
        logger.info('ParsecDisplay is already running')
        return
    if not os.path.isfile(PARSEC_EXE):
        raise ParsecVddError(f'ParsecDisplay.exe not found: {PARSEC_EXE}')
    logger.info(f'Starting ParsecDisplay: {PARSEC_EXE}')
    # 与 game_control.start_program 一致：脱离桌面壳的 Job Object，避免脚本退出时被连带终止
    creationflags = subprocess.CREATE_BREAKAWAY_FROM_JOB | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([PARSEC_EXE, '-silent'], cwd=PARSEC_DIR, creationflags=creationflags)
    except OSError as e:
        raise ParsecVddError(f'Failed to start ParsecDisplay: {e}')


def stop_app(timeout=10):
    """结束 ParsecDisplay 常驻进程，驱动随后会自动移除所有虚拟屏"""
    processes = _iter_app_processes()
    if not processes:
        logger.info('ParsecDisplay is not running')
        return
    for proc, path in processes:
        # 内置副本之外的实例也能关掉，但要留下痕迹便于排查
        if path and os.path.normcase(os.path.normpath(path)) != os.path.normcase(os.path.normpath(PARSEC_EXE)):
            logger.warning(f'Stopping ParsecDisplay running outside NKAS: {path}')
        try:
            proc.terminate()
            proc.wait(timeout)
            logger.info(f'ParsecDisplay stopped (pid={proc.pid})')
        except psutil.TimeoutExpired:
            logger.warning(f'ParsecDisplay pid={proc.pid} did not exit in {timeout}s, killing')
            proc.kill()
        except psutil.NoSuchProcess:
            continue


def list_displays() -> list:
    """
    列出已添加的 Parsec 虚拟屏。

    Returns:
        list[dict]: [{'index', 'device', 'number', 'name', 'width', 'height', 'hz', 'orientation'}]
    """
    output = _cli('list')
    if 'No virtual displays present' in output:
        return []

    displays = []
    current = None
    for line in output.splitlines():
        line = line.strip()
        match = re.match(r'^Index:\s*(\d+)', line)
        if match:
            current = {
                'index': int(match.group(1)), 'device': '', 'number': None,
                'name': '', 'width': None, 'height': None, 'hz': None, 'orientation': None,
            }
            displays.append(current)
            continue
        if current is None:
            continue

        match = re.match(r'^-\s*Device:\s*(\S+)', line)
        if match:
            current['device'] = match.group(1)
            continue
        match = re.match(r'^-\s*Number:\s*(\d+)', line)
        if match:
            current['number'] = int(match.group(1))
            continue
        match = re.match(r'^-\s*Name:\s*(\S+)', line)
        if match:
            current['name'] = match.group(1)
            continue
        # '1080 × 1920 @ 60 Hz'，分隔符随编码变化，用 \D+ 兜住
        match = re.match(r'^-\s*Mode:\s*(\d+)\D+(\d+)\s*@\s*(\d+)', line)
        if match:
            current['width'], current['height'], current['hz'] = (int(g) for g in match.groups())
            continue
        match = re.match(r'^-\s*Orientation:\s*(\w+)', line)
        if match:
            current['orientation'] = match.group(1).lower()

    if not displays:
        raise ParsecVddError(f'Unexpected ParsecDisplay -cli list output: {output.strip()}')
    return displays


def find_screen_n():
    """
    返回虚拟屏在 EnumDisplayMonitors 中的下标（与 GUI 屏幕下拉顺序一致）。

    `-cli list` 的 Device（如 `\\\\.\\DISPLAY38`）与 GetMonitorInfo 的
    Device 字段一致，据此把 VDD 屏映射回活动显示器下标。

    Returns:
        int | None: 解析失败返回 None
    """
    import win32api

    displays = list_displays()
    if not displays:
        logger.warning('No Parsec virtual display is present')
        return None
    wanted = {str(d['device']).upper() for d in displays if d.get('device')}
    if not wanted:
        logger.warning('ParsecDisplay reported displays without a device name')
        return None

    for n, monitor in enumerate(win32api.EnumDisplayMonitors()):
        device = str(win32api.GetMonitorInfo(monitor[0]).get('Device', '')).upper()
        if device in wanted:
            return n

    logger.warning(f'Parsec VDD display {sorted(wanted)} not found in EnumDisplayMonitors')
    return None


def _wait_monitor_count(expected, timeout=WAIT_DISPLAY_TIMEOUT, interval=0.5) -> bool:
    """等待活动显示器数量达到预期（启用/创建屏幕有几秒延迟）"""
    import win32api

    end_time = time.time() + timeout
    while time.time() < end_time:
        if len(win32api.EnumDisplayMonitors()) >= expected:
            return True
        time.sleep(interval)
    logger.warning(f'Timed out waiting for Parsec virtual display after {timeout}s')
    return False


def _set_mode_1080p_portrait(device_name) -> bool:
    """
    用 ChangeDisplaySettingsEx 把指定显示设备设为 1080x1920@60 竖屏。

    `vdd add` 默认产生 1920x1080@60 横屏，且驱动不提供 1080x1920 的预置模式；
    但直接按设备名提交 orientation=1 + 宽高互换的模式可以被驱动接受
    （与 game_control.screen_rotate 的做法一致，这里改成按设备名操作）。

    Returns:
        bool: 是否已处于目标模式（含无需修改的情况）
    """
    import win32api
    import win32con

    dm = win32api.EnumDisplaySettings(device_name, win32con.ENUM_CURRENT_SETTINGS)
    if (dm.PelsWidth, dm.PelsHeight, dm.DisplayOrientation) == (
        TARGET_WIDTH, TARGET_HEIGHT, TARGET_ORIENTATION
    ):
        logger.info(f'Parsec VDD already at {TARGET_WIDTH}x{TARGET_HEIGHT} portrait')
        return True

    logger.info(
        f'Setting Parsec VDD to {TARGET_WIDTH}x{TARGET_HEIGHT}@'
        f'{TARGET_HZ} portrait (current {dm.PelsWidth}x{dm.PelsHeight} '
        f'orientation={dm.DisplayOrientation})'
    )
    # 优先按目标刷新率提交，失败则沿用当前刷新率再试一次
    for hz in dict.fromkeys([TARGET_HZ, dm.DisplayFrequency]):
        if hz:
            dm.DisplayFrequency = hz
        dm.PelsWidth, dm.PelsHeight = TARGET_WIDTH, TARGET_HEIGHT
        dm.DisplayOrientation = TARGET_ORIENTATION
        result = win32api.ChangeDisplaySettingsEx(device_name, dm)
        if result == win32con.DISP_CHANGE_SUCCESSFUL:
            # 回读确认，避免驱动接受请求但未真正生效
            applied = win32api.EnumDisplaySettings(device_name, win32con.ENUM_CURRENT_SETTINGS)
            if (applied.PelsWidth, applied.PelsHeight) == (TARGET_WIDTH, TARGET_HEIGHT):
                logger.info(f'Parsec VDD mode applied: {applied.PelsWidth}x{applied.PelsHeight}@{hz}Hz')
                return True
            logger.warning(
                f'Parsec VDD mode did not stick: got {applied.PelsWidth}x{applied.PelsHeight}'
            )
        else:
            logger.warning(f'ChangeDisplaySettingsEx failed with code {result} (hz={hz})')

    logger.error(f'Failed to set Parsec VDD to {TARGET_WIDTH}x{TARGET_HEIGHT} portrait')
    return False


def ensure_screen_1080p_portrait():
    """
    确保存在一块 1080x1920@60 竖屏的 Parsec 虚拟屏，并返回其屏幕下标。

    流程：检查驱动 -> 拉起常驻进程（RestoreDisplays 可能自动恢复出旧屏幕）
    -> 没有屏幕则 add -> 校正为 1080p 竖屏 -> 解析屏幕下标。

    Returns:
        int | None: 屏幕在 EnumDisplayMonitors 中的下标，解析失败返回 None

    Raises:
        ParsecVddError: 驱动未安装、进程/屏幕创建失败
    """
    import win32api

    logger.hr('Parsec VDD enable', level=2)

    status = driver_status()
    if not status['installed']:
        raise ParsecVddError(f'Parsec VDD 驱动不可用，请先安装官方 Parsec VDD 驱动。{status["message"]}')
    logger.attr('ParsecVDD', f'driver {status["version"] or status["status"]}')

    baseline = len(win32api.EnumDisplayMonitors())
    started_here = not is_app_running()
    try:
        if started_here:
            start_app()
            # -silent 会按注册表快照重建之前用过的屏幕，等待其出现
            _wait_monitor_count(baseline + 1)
        else:
            logger.info('ParsecDisplay is already running')

        if not list_displays():
            logger.info('No Parsec virtual display, adding one')
            _cli('add')
            if not _wait_monitor_count(baseline + 1):
                raise ParsecVddError('Failed to create a Parsec virtual display')
            # 屏幕刚出现时 CLI 列表可能还没同步，留出重试余量
            for _ in range(5):
                if list_displays():
                    break
                time.sleep(0.5)

        displays = list_displays()
        for display in displays:
            if display.get('device'):
                _set_mode_1080p_portrait(display['device'])

        screen_n = find_screen_n()
        if screen_n is None:
            logger.warning('Parsec VDD screen index not resolved, falling back to configured ScreenNumber')
        else:
            logger.info(f'Parsec VDD screen index: {screen_n}')
        return screen_n
    except Exception:
        if started_here:
            try:
                stop_app()
            except Exception as cleanup_error:
                logger.warning(f'Failed to clean up ParsecDisplay after enable failed: {cleanup_error}')
        raise


def auto_stop():
    """任务结束后关闭 Parsec 虚拟屏（结束常驻进程，驱动约 1 秒后移除屏幕）"""
    logger.hr('Parsec VDD stop', level=2)
    try:
        stop_app()
    except (ParsecVddError, psutil.Error) as e:
        logger.warning(f'Failed to stop ParsecDisplay: {e}')
