"""虚拟鼠标驱动的安装与状态探测。

driver 控制方案（module/device/win/virtual_mouse/driver_mouse.py）依赖系统中的
虚拟鼠标 HID 设备。本模块提供两件事：
- 探测设备接口是否存在（驱动是否已安装），只打开句柄不发 IOCTL，无副作用；
- 调用 bin/virtual_mouse/virtual-mouse-driver-manager.ps1 完成安装/卸载
  （复制 depot + 运行安装器）。安装与卸载需要管理员权限，由 NKAS 自身的管理员
  权限保证，脚本不再自行提权。
"""

import json
import os
import subprocess

from module.logger import logger

MANAGER_SCRIPT = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '../../bin/virtual_mouse/virtual-mouse-driver-manager.ps1'))
BUNDLED_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '../../bin/virtual_mouse/driver_hid_virtual'))
BUNDLED_MANIFEST = os.path.join(BUNDLED_DIR, 'manifest.json')

# 虚拟鼠标设备接口（与 driver_mouse.py 相同，这里独立保留一份避免在非 Windows
# 平台 import driver_mouse 时加载 WinDLL 失败）
VIRTUAL_MOUSE_INTERFACE_GUID = '{1abc05c0-c378-41b9-9cef-df1aba82b015}'
DEVICE_INDEX_RANGE = range(10)

GENERIC_READ_WRITE = 0xC0000000
FILE_SHARE_BOTH = 0x00000003
OPEN_EXISTING = 3


class VirtualMouseDriverError(Exception):
    """用户可读的安装/探测错误，消息直接展示在前端。"""


def probe_device():
    """返回第一个能打开的虚拟鼠标设备接口路径；不存在返回 None。

    只做 CreateFileW 打开/关闭，不发送任何报告，因此不会移动光标或按键。
    """
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    invalid = wintypes.HANDLE(-1).value
    for index in DEVICE_INDEX_RANGE:
        path = rf'\??\ROOT#SYSTEM#000{index}#{VIRTUAL_MOUSE_INTERFACE_GUID}'
        handle = kernel32.CreateFileW(path, GENERIC_READ_WRITE, FILE_SHARE_BOTH, None, OPEN_EXISTING, 0, None)
        if handle == invalid:
            continue
        kernel32.CloseHandle(handle)
        return path
    return None


def bundled_version():
    """捆绑驱动包 manifest 中的版本号；读不到返回空串。"""
    try:
        with open(BUNDLED_MANIFEST, 'r', encoding='utf-8') as f:
            data = json.load(f)
        extensions = data.get('installer', {}).get('extensions', [])
        for ext in extensions:
            if ext.get('name') == 'virtual_hid':
                return str(ext.get('version') or '')
    except (OSError, ValueError, AttributeError):
        pass
    return ''


def driver_status():
    """
    Returns:
        dict: {
            'supported': bool,   # 仅 Windows 支持
            'installed': bool,   # 虚拟鼠标设备接口是否可打开
            'device': str,       # 命中的设备接口路径（未安装为空串）
            'bundled': bool,     # 项目内是否带有安装文件
            'version': str,      # 捆绑驱动包版本（如 2026.0.0.0）
        }
    """
    device = probe_device()
    return {
        'supported': os.name == 'nt',
        'installed': device is not None,
        'device': device or '',
        'bundled': os.path.isfile(os.path.join(BUNDLED_DIR, 'virtual_driver_manager.exe')),
        'version': bundled_version(),
    }


def _run_manager(action, timeout=120):
    """调用 virtual-mouse-driver-manager.ps1 并解析其 JSON 行输出（与 vdd._run_manager 同约定）。"""
    if os.name != 'nt':
        raise VirtualMouseDriverError('Virtual mouse driver management is only supported on Windows')
    if not os.path.isfile(MANAGER_SCRIPT):
        raise VirtualMouseDriverError(f'Driver manager script not found: {MANAGER_SCRIPT}')
    try:
        result = subprocess.run(
            [
                'powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', MANAGER_SCRIPT, '-Action', action, '-Json', '-Silent',
            ],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise VirtualMouseDriverError(f'Driver {action} timed out after {timeout}s')
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
        raise VirtualMouseDriverError(f'Driver {action} failed (exit {result.returncode}): {output.strip()}')
    return records


def _raise_on_error(records, action):
    """脚本以 error 记录回传失败原因；有则直接抛给前端。"""
    for record in records:
        if record.get('status') == 'error':
            raise VirtualMouseDriverError(record.get('message') or f'Driver {action} failed')


def install_driver():
    """复制捆绑 depot 到 %ProgramData%\\LGHUB 并运行安装器。

    需要 NKAS 本身以管理员权限运行（脚本不再自行提权）。成败以安装后的
    设备探测为准，而不是安装器自报的结果。

    Raises:
        VirtualMouseDriverError: 当前进程不是管理员、安装器返回非零，或安装后探测不到设备。
    """
    records = _run_manager('install')
    _raise_on_error(records, 'install')
    if probe_device() is not None:
        logger.info('Virtual mouse driver install: device detected after install')
        return
    raise VirtualMouseDriverError('Driver installer ran but no device was detected afterwards')


def uninstall_driver():
    """移除已安装的虚拟鼠标驱动。

    与 install_driver 同理，需要管理员权限，成败以卸载后的设备探测为准。

    Raises:
        VirtualMouseDriverError: 当前进程不是管理员、卸载器返回非零，或卸载后设备仍在。
    """
    records = _run_manager('uninstall')
    _raise_on_error(records, 'uninstall')
    if probe_device() is None:
        logger.info('Virtual mouse driver uninstall: device no longer present')
        return
    raise VirtualMouseDriverError('Driver uninstaller ran but the device is still present')
