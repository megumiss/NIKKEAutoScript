import json
import os
import subprocess
import time

from module.logger import logger

VDD_SCRIPT = os.path.normpath(os.path.join(os.path.dirname(__file__), '../../../bin/vdd/virtual-driver-manager.ps1'))


class VddError(Exception):
    pass


def _run_manager(action, timeout=60):
    """
    调用 virtual-driver-manager.ps1 并解析其 JSON 行输出。

    Returns:
        list[dict]: 输出中的全部 JSON 行
    """
    if os.name != 'nt':
        raise VddError('VDD management is only supported on Windows')
    if not os.path.isfile(VDD_SCRIPT):
        raise VddError(f'VDD manager script not found: {VDD_SCRIPT}')
    try:
        result = subprocess.run(
            [
                'powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', VDD_SCRIPT, '-Action', action, '-Json', '-Silent',
            ],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise VddError(f'VDD {action} timed out after {timeout}s')
    output = result.stdout + result.stderr
    records = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if result.returncode != 0 and not records:
        raise VddError(f'VDD {action} failed (exit {result.returncode}): {output.strip()}')
    return records


def vdd_status():
    """
    Returns:
        dict: {'installed': bool, 'status': 'enabled'|'disabled'|...}
    """
    records = _run_manager('status')
    for record in records:
        if 'installed' in record:
            return record
    raise VddError(f'Unexpected VDD status output: {records}')


def _expect_success(action):
    records = _run_manager(action)
    for record in records:
        if record.get('status') == 'success':
            logger.info(f'VDD {action}: {record.get("message", "ok")}')
            return
    raise VddError(f'VDD {action} failed: {records}')


def vdd_enable():
    _expect_success('enable')


def vdd_disable():
    _expect_success('disable')


def wait_vdd_monitor(baseline_count, timeout=15):
    """
    启用虚拟屏后屏幕重现有几秒延迟，轮询等待活动显示器数量增加。

    Args:
        baseline_count: 启用前的活动显示器数量
    """
    import win32api

    end_time = time.time() + timeout
    while time.time() < end_time:
        if len(win32api.EnumDisplayMonitors()) > baseline_count:
            return True
        time.sleep(0.5)
    logger.warning(f'Timed out waiting for VDD monitor to appear after {timeout}s')
    return False


def _mttvdd_auto_start():
    """任务启动时启用 MttVDD 虚拟屏并等待其出现"""
    import win32api

    logger.hr('VDD enable', level=2)
    try:
        if vdd_status().get('status') == 'enabled':
            logger.info('VDD screen is already enabled')
            return
    except VddError as e:
        logger.warning(f'VDD status check failed, try to enable anyway: {e}')
    baseline = len(win32api.EnumDisplayMonitors())
    vdd_enable()
    wait_vdd_monitor(baseline)


def _mttvdd_auto_stop():
    """任务结束后禁用 MttVDD 虚拟屏"""
    try:
        logger.hr('VDD disable', level=2)
        vdd_disable()
    except VddError as e:
        logger.warning(e)


# 虚拟屏驱动在显示设备上的常见标识。MttVDD 即项目内置的 virtual-driver-manager.ps1，
# 另外两个是同一类 Indirect Display Driver 的通用名，命中其一即可认为是虚拟屏。
VDD_DEVICE_MARKERS = ('MttVDD', 'IddSampleDriver', 'Virtual Display Driver')

# DISPLAY_DEVICE_ATTACHED_TO_DESKTOP：只有接入桌面的设备才可能出现在 EnumDisplayMonitors 里
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1

# EnumDisplayDevices 越界上限，防止驱动异常时不返回错误而死循环
MAX_DISPLAY_DEVICES = 256


def _enum_display_devices(device=None, index=0):
    """
    EnumDisplayDevices 包装，越界（pywintypes.error）时返回 None。

    pywin32 在索引超出范围时抛异常而不是返回空，因此这里统一收敛成 None。
    """
    import pywintypes
    import win32api

    try:
        return win32api.EnumDisplayDevices(device, index)
    except pywintypes.error:
        return None


def find_screen_n():
    """
    返回 MttVDD 虚拟屏在 EnumDisplayMonitors 中的下标（与 GUI 屏幕下拉顺序一致）。

    虚拟屏标识可能出现在适配器层（DeviceString/DeviceID），也可能出现在其下的
    显示器层，两处都查；再按 DeviceName 与 GetMonitorInfo 的 Device 字段对齐，
    避免直接使用 EnumDisplayDevices 索引（它会枚举到未接入桌面的"幽灵"设备）。

    Returns:
        int | None: 解析失败返回 None
    """
    import win32api

    wanted = set()
    for i in range(MAX_DISPLAY_DEVICES):
        adapter = _enum_display_devices(None, i)
        if adapter is None:
            break
        if not adapter.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            continue

        candidates = [adapter.DeviceString, adapter.DeviceID]
        monitor = _enum_display_devices(adapter.DeviceName, 0)
        if monitor is not None:
            candidates.append(monitor.DeviceString)
        text = ' '.join(str(c) for c in candidates if c).lower()
        if any(marker.lower() in text for marker in VDD_DEVICE_MARKERS):
            wanted.add(str(adapter.DeviceName).upper())

    if not wanted:
        logger.warning(f'No active VDD display matched {VDD_DEVICE_MARKERS}')
        return None

    for n, monitor in enumerate(win32api.EnumDisplayMonitors()):
        device = str(win32api.GetMonitorInfo(monitor[0]).get('Device', '')).upper()
        if device in wanted:
            return n

    logger.warning(f'VDD display {sorted(wanted)} not found in EnumDisplayMonitors')
    return None


def vdd_auto_start(config):
    """
    任务启动时按 config.PCClient_VddType 分发到对应的虚拟屏实现。

    MttVDD 分支 = 内置脚本启用 + 解析屏幕序号；
    ParsecVDD 分支 = 拉起内置 ParsecDisplay 并确保 1080p 竖屏。

    Args:
        config (NikkeConfig):

    Returns:
        int | None: 屏幕在 EnumDisplayMonitors 中的下标，解析失败返回 None
    """
    if _vdd_type(config) == 'parsecvdd':
        from module.device.win import parsec_vdd

        return parsec_vdd.ensure_screen_1080p_portrait()

    _mttvdd_auto_start()
    return find_screen_n()


def vdd_auto_stop(config):
    """任务结束后按 config.PCClient_VddType 分发停止逻辑"""
    if _vdd_type(config) == 'parsecvdd':
        from module.device.win import parsec_vdd

        parsec_vdd.auto_stop()
        return

    _mttvdd_auto_stop()


def vdd_find_screen_n(config):
    """
    只解析虚拟屏序号，不触发启停。

    用于 VddScreen=true 但 VddAutoManage=false 的场景：用户自行常驻开着
    虚拟屏，NKAS 只需要定位它在 EnumDisplayMonitors 中的下标。

    Returns:
        int | None: 解析失败返回 None
    """
    if _vdd_type(config) == 'parsecvdd':
        from module.device.win import parsec_vdd

        return parsec_vdd.find_screen_n()

    return find_screen_n()


def _vdd_type(config):
    """读取 VddType 并归一化，缺省按默认值 parsecvdd 处理"""
    value = getattr(config, 'PCClient_VddType', None)
    return str(value or 'parsecvdd').lower()
