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


def vdd_auto_start():
    """任务启动时启用 VDD 虚拟屏并等待其出现"""
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


def vdd_auto_stop():
    """任务结束后禁用 VDD 虚拟屏"""
    try:
        logger.hr('VDD disable', level=2)
        vdd_disable()
    except VddError as e:
        logger.warning(e)
