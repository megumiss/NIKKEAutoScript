"""虚拟鼠标驱动的安装与状态探测。

driver 控制方案（module/device/win/virtual_mouse/driver_mouse.py）依赖系统中的
虚拟鼠标 HID 设备。本模块提供两件事：
- 探测设备接口是否存在（驱动是否已安装），只打开句柄不发 IOCTL，无副作用；
- 调用 bin/virtual_mouse/virtual-mouse-driver-manager.ps1 完成安装/卸载
  （复制 depot + 运行安装器）。安装与卸载需要管理员权限，由 NKAS 自身的管理员
  权限保证，脚本不再自行提权。

设备接口靠 SetupAPI 按接口类 GUID 枚举真实路径，不按 ROOT#SYSTEM#000N 猜序号：
该序号取决于本机已存在的根枚举设备，实测有机器上是 0002 而非 0001。
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

GENERIC_READ_WRITE = 0xC0000000
FILE_SHARE_BOTH = 0x00000003
OPEN_EXISTING = 3

# SetupAPI 标志：只枚举当前存在的设备接口
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

# 驱动包落地后的文件特征：DriverStore 里的包目录 + System32\drivers 下的镜像
DRIVER_STORE_PREFIX = 'logi_joy'
DRIVER_IMAGE_PREFIX = 'logi_joy'


class VirtualMouseDriverError(Exception):
    """用户可读的安装/探测错误，消息直接展示在前端。"""


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        """GUID 结构体。文本里的 Data1..3 是数值，存进结构体时由 ctypes 转成小端。"""

        _fields_ = [
            ('Data1', ctypes.c_ulong),
            ('Data2', ctypes.c_ushort),
            ('Data3', ctypes.c_ushort),
            ('Data4', ctypes.c_ubyte * 8),
        ]

        @classmethod
        def parse(cls, text):
            """从 '{1abc05c0-c378-41b9-9cef-df1aba82b015}' 取得 GUID 结构体。"""
            raw = bytes.fromhex(text.strip('{}').replace('-', ''))
            return cls(
                int.from_bytes(raw[0:4], 'big'),
                int.from_bytes(raw[4:6], 'big'),
                int.from_bytes(raw[6:8], 'big'),
                (ctypes.c_ubyte * 8)(*raw[8:16]),
            )

    class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.DWORD),
            ('InterfaceClassGuid', _GUID),
            ('Flags', wintypes.DWORD),
            ('Reserved', ctypes.c_void_p),
        ]


def _listdir(path):
    """列目录；不存在或无权限时返回空列表。"""
    try:
        return os.listdir(path)
    except OSError:
        return []


def enum_interface_paths():
    """枚举虚拟鼠标设备接口，返回接口符号链接的真实路径。

    只列举，不打开、不发报告。返回空列表表示系统中没有注册该接口的设备实例。

    Returns:
        list[str]: 例如 [r'\\??\\ROOT#SYSTEM#0001#{1abc05c0-...}']；没有设备时为空。
    """
    if os.name != 'nt':
        return []

    setupapi = ctypes.WinDLL('setupapi', use_last_error=True)
    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD,
    ]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

    interface_class = _GUID.parse(VIRTUAL_MOUSE_INTERFACE_GUID)
    info_set = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(interface_class), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if info_set == wintypes.HANDLE(-1).value:
        logger.warning(f'SetupDiGetClassDevs failed for {VIRTUAL_MOUSE_INTERFACE_GUID}')
        return []

    # SP_DEVICE_INTERFACE_DETAIL_DATA 的头长度按位宽取值（32 位 6、64 位 8）；
    # DevicePath 紧跟在 4 字节的 cbSize 之后。
    detail_header_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
    path_offset = ctypes.sizeof(wintypes.DWORD)
    paths = []
    try:
        index = 0
        while True:
            interface_data = _SP_DEVICE_INTERFACE_DATA()
            interface_data.cbSize = ctypes.sizeof(interface_data)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    info_set, None, ctypes.byref(interface_class), index, ctypes.byref(interface_data)):
                break
            # 首次调用必然因缓冲区不足失败，用它取所需长度
            required = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set, ctypes.byref(interface_data), None, 0, ctypes.byref(required), None)
            if required.value:
                buffer = ctypes.create_string_buffer(required.value)
                ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[0] = detail_header_size
                if setupapi.SetupDiGetDeviceInterfaceDetailW(
                        info_set, ctypes.byref(interface_data), buffer, required.value, None, None):
                    paths.append(ctypes.wstring_at(ctypes.addressof(buffer) + path_offset))
            index += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info_set)
    return paths


def open_device(path):
    """打开一次接口路径并在成功后立即关闭；不发任何报告，因此不会移动光标或按键。"""
    if os.name != 'nt':
        return False
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(path, GENERIC_READ_WRITE, FILE_SHARE_BOTH, None, OPEN_EXISTING, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        return False
    kernel32.CloseHandle(handle)
    return True


def probe_device():
    """返回第一个能打开的虚拟鼠标设备接口路径；不存在返回 None。

    只做 CreateFileW 打开/关闭，不发送任何报告，因此不会移动光标或按键。
    """
    for path in enum_interface_paths():
        if open_device(path):
            return path
    return None


def driver_package_present():
    """驱动包是否已落地到系统（DriverStore 包目录 + System32\\drivers 镜像）。

    只读文件系统。用来区分两种失败：安装器完全没生效 vs 驱动装上了但设备接口没注册 ——
    两者的用户动作完全不同（前者重试/查拦截，后者重装无用、要重建设备栈）。
    """
    if os.name != 'nt':
        return False
    root = os.environ.get('SystemRoot', r'C:\Windows')
    images = _listdir(os.path.join(root, 'System32', 'drivers'))
    if not any(name.startswith(DRIVER_IMAGE_PREFIX) and name.endswith('.sys') for name in images):
        return False
    store = _listdir(os.path.join(root, 'System32', 'DriverStore', 'FileRepository'))
    return any(name.startswith(DRIVER_STORE_PREFIX) for name in store)


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
            'package': bool,     # 驱动包是否已落地到系统
            'interfaces': int,   # 枚举到的接口实例数（0 且 package 为真 = 接口未注册）
        }
    """
    paths = enum_interface_paths()
    device = next((path for path in paths if open_device(path)), None)
    return {
        'supported': os.name == 'nt',
        'installed': device is not None,
        'device': device or '',
        'bundled': os.path.isfile(os.path.join(BUNDLED_DIR, 'virtual_driver_manager.exe')),
        'version': bundled_version(),
        'package': driver_package_present(),
        'interfaces': len(paths),
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


def _reboot_requested(records):
    """安装器是否报告了「文件已就位、重启后生效」（DiInstallDriverW 返回
    ERROR_SUCCESS_REBOOT_REQUIRED，安装器把它打印成 NEED_REBOOT，脚本解析成该字段）。

    这是唯一能区分「安装失败」和「装好了、只等重启」的信号。
    """
    return any(record.get('reboot_required') for record in records)


def install_driver():
    """复制捆绑 depot 到 %ProgramData%\\LGHUB 并运行安装器。

    需要 NKAS 本身以管理员权限运行（脚本不再自行提权）。成败以安装后的设备探测为准，
    而不是安装器自报的结果；失败时按「是否需要重启」和「驱动包是否落地」给出不同的
    指引 —— 重装对「驱动包已装但接口未注册」这一种完全无效。

    Returns:
        dict: {'reboot_required': bool, 'message': str}。reboot_required 为真表示
            设备虽然在，但驱动切换被 Windows 推迟到重启后才生效，message 是给用户的
            提示，API 层应把它透传到前端。

    Raises:
        VirtualMouseDriverError: 当前进程不是管理员、安装器返回非零，或安装后探测不到设备。
    """
    records = _run_manager('install')
    _raise_on_error(records, 'install')
    reboot_required = _reboot_requested(records)
    paths = enum_interface_paths()
    device = next((path for path in paths if open_device(path)), None)
    if device is not None:
        if reboot_required:
            logger.warning(f'Virtual mouse driver install: device present ({device}), '
                           'but the driver swap is pending a reboot')
            return {
                'reboot_required': True,
                'message': 'Driver installed, but Windows finishes swapping the driver '
                           'only after a reboot. Restart Windows for the new driver to take effect.',
            }
        logger.info(f'Virtual mouse driver install: device detected ({device})')
        return {'reboot_required': False}
    if reboot_required:
        # 驱动文件被运行中的驱动占用，Windows 把替换推迟到下次启动：设备栈仍挂着旧驱动，
        # 接口不会出现，此时再点安装只会重复同一结果。
        raise VirtualMouseDriverError(
            'Driver files are installed, but Windows deferred the driver swap until the next start, '
            'so the virtual mouse device has not appeared yet. Restart the bus device '
            '(pnputil /restart-device) or reboot Windows, then retry. Reinstalling without rebooting '
            'leaves it in the same state.')
    if paths:
        raise VirtualMouseDriverError(
            f'Virtual mouse interfaces exist ({len(paths)}) but none can be opened: {paths[0]}. '
            'Another process may be holding the device, or the filter driver failed to start. '
            'Close other NKAS instances and reboot, then retry.')
    if not driver_package_present():
        raise VirtualMouseDriverError(
            'Driver installer ran but the driver package is not present afterwards. '
            'Retry the install; if it keeps failing, check whether security software blocked the driver files.')
    raise VirtualMouseDriverError(
        'Driver package is installed but the virtual mouse interface is not registered. '
        'Reinstalling does not help: the interface is registered by the logi_joy_xlcore upper filter '
        'when the bus device starts, and reinstalling never rebuilds a running device stack. '
        'Restart the bus device (pnputil /restart-device) or reboot Windows, then retry.')


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
