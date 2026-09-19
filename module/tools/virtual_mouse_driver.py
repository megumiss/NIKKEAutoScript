"""虚拟鼠标驱动的安装与状态探测。

driver 控制方案（module/device/win/virtual_mouse/driver_mouse.py）依赖系统中的
虚拟鼠标 HID 设备。本模块提供两件事：
- 探测设备接口是否存在（驱动是否已安装），只打开句柄不发 IOCTL，无副作用；
- 调用 bin/virtual_mouse/virtual-mouse-driver-manager.ps1 完成安装/卸载
  （复制 depot + 运行安装器）。安装与卸载需要管理员权限，由 NKAS 自身的管理员
  权限保证，脚本不再自行提权。

设备接口靠 SetupAPI 按接口类 GUID 枚举真实路径，不按 ROOT#SYSTEM#000N 猜序号：
该序号取决于本机已存在的根枚举设备，实测有机器上是 0002 而非 0001。

接口能打开不等于通道可用：接口收到的报告由总线上的 logi_joy_xlcore 过滤驱动转发给
HID 子设备，子设备变成幽灵设备（Windows 代码 45）时接口照旧存在、IOCTL 照旧返回成功，
只是报告被静默丢弃。因此凡是判定「通道可用」的地方都同时看 sub_device_present()。
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

# CM_Get_Device_ID_ListW 的过滤标志与返回值
CM_GETIDLIST_FILTER_ENUMERATOR = 0x00000001
CM_GETIDLIST_FILTER_PRESENT = 0x00000100
CR_SUCCESS = 0

# G HUB 虚拟总线（logi_joy_bus_enum）枚举出的 HID 子设备，鼠标 C231 / 键盘 C232。
# 接口由挂在同一总线上的 xlcore 过滤驱动注册，子设备才是报告的实际消费者。
VIRTUAL_HID_ENUMERATOR = 'LGHUBDevice'

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

    注意打开成功不证明通道可用，幽灵设备（代码 45）下同样能打开，判据见 sub_device_present()。
    """
    for path in enum_interface_paths():
        if open_device(path):
            return path
    return None


def _device_instance_ids(enumerator, present_only=False):
    """列出某个枚举器下的设备实例 ID。

    present_only 为假时包含幽灵设备（Windows 代码 45，即曾经存在、当前未呈现）。

    Args:
        enumerator: 枚举器名，例如 'LGHUBDevice'。
        present_only: 只统计实际呈现的设备。

    Returns:
        list[str]: 设备实例 ID；枚举器不存在或调用失败时为空列表。
    """
    if os.name != 'nt':
        return []
    cfgmgr32 = ctypes.WinDLL('cfgmgr32', use_last_error=True)
    cfgmgr32.CM_Get_Device_ID_List_SizeW.restype = ctypes.c_ulong
    cfgmgr32.CM_Get_Device_ID_List_SizeW.argtypes = [
        ctypes.POINTER(ctypes.c_ulong), wintypes.LPCWSTR, ctypes.c_ulong,
    ]
    cfgmgr32.CM_Get_Device_ID_ListW.restype = ctypes.c_ulong
    cfgmgr32.CM_Get_Device_ID_ListW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
    ]
    flags = CM_GETIDLIST_FILTER_ENUMERATOR
    if present_only:
        flags |= CM_GETIDLIST_FILTER_PRESENT
    size = ctypes.c_ulong()
    if cfgmgr32.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), enumerator, flags) != CR_SUCCESS:
        return []
    if not size.value:
        return []
    buffer = ctypes.create_unicode_buffer(size.value)
    if cfgmgr32.CM_Get_Device_ID_ListW(enumerator, buffer, size.value, flags) != CR_SUCCESS:
        return []
    return [item for item in buffer[:].split('\0') if item]


def sub_device_present():
    """虚拟鼠标的 HID 子设备是否实际呈现。纯 CM 查询，无副作用。

    Returns:
        bool: True 表示子设备存在，报告能被投递；
        False 表示总线有该设备的记录但全部未呈现（幽灵设备，代码 45），此时接口和
            IOCTL 都正常却没有任何输入，需要重建设备节点；
        None 表示系统中没有该枚举器（未安装这套驱动体系），此处不构成判据。
    """
    if os.name != 'nt':
        return None
    if _device_instance_ids(VIRTUAL_HID_ENUMERATOR, present_only=True):
        return True
    if not _device_instance_ids(VIRTUAL_HID_ENUMERATOR):
        return None
    return False


def driver_package_present():
    """驱动包是否已落地到系统（DriverStore 包目录 + System32\\drivers 镜像）。

    只读文件系统。用来区分两种失败：安装器完全没生效 vs 驱动装上了但设备接口没注册。
    后者包含重启后设备变隐藏的场景（见 repair_driver），重跑一次安装即可恢复。
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
            'sub_device': bool or None,  # HID 子设备是否呈现（False = 幽灵设备，代码 45）
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
        'sub_device': sub_device_present(),
    }


def _run_manager(action, timeout=120, skip_copy=False):
    """调用 virtual-mouse-driver-manager.ps1 并解析其 JSON 行输出（与 vdd._run_manager 同约定）。

    skip_copy 仅对 install 有效：跳过复制捆绑 depot，直接运行已就位的安装器。
    """
    if os.name != 'nt':
        raise VirtualMouseDriverError('Virtual mouse driver management is only supported on Windows')
    if not os.path.isfile(MANAGER_SCRIPT):
        raise VirtualMouseDriverError(f'Driver manager script not found: {MANAGER_SCRIPT}')
    command = [
        'powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', MANAGER_SCRIPT, '-Action', action, '-Json', '-Silent',
    ]
    if skip_copy and action == 'install':
        command.append('-SkipCopy')
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
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


def install_driver(skip_copy=False):
    """复制捆绑 depot 到 %ProgramData%\\LGHUB 并运行安装器。

    需要 NKAS 本身以管理员权限运行（脚本不再自行提权）。成败以安装后的设备探测为准，
    而不是安装器自报的结果；探测同时看接口能否打开和 HID 子设备是否呈现，失败时按
    「子设备未呈现」「是否需要重启」「驱动包是否落地」给出不同的指引。
    重跑安装可以重建消失的设备接口（重启后设备变隐藏的场景，见 repair_driver）。

    Args:
        skip_copy: depot 已在 %ProgramData%\\LGHUB 就位时跳过复制，直接运行其中的安装器
            （供 repair_driver 使用）；脚本会逐文件校验与捆绑 depot 一致后才跳过，
            缺失或版本不同步时仍会复制。

    Returns:
        dict: {'reboot_required': bool, 'message': str}。reboot_required 为真表示
            设备虽然在，但驱动切换被 Windows 推迟到重启后才生效，message 是给用户的
            提示，API 层应把它透传到前端。

    Raises:
        VirtualMouseDriverError: 当前进程不是管理员、安装器返回非零，或安装后探测不到设备。
    """
    records = _run_manager('install', skip_copy=skip_copy)
    _raise_on_error(records, 'install')
    reboot_required = _reboot_requested(records)
    paths = enum_interface_paths()
    device = next((path for path in paths if open_device(path)), None)
    # 接口能打开不代表通道可用：报告由 xlcore 转发给 HID 子设备，子设备是幽灵设备
    # （代码 45）时报告被丢弃、光标不动。子设备判不出来（None）时不改既有行为。
    sub_device_missing = sub_device_present() is False
    if device is not None and not sub_device_missing:
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
    if sub_device_missing:
        # 安装器刚跑完子设备仍是幽灵设备，说明它重建设备节点没能生效（常见于驱动文件被
        # 运行中的驱动占用、Windows 把替换推迟到下次启动），只剩重启这一条路。
        raise VirtualMouseDriverError(
            'The virtual mouse interface is present but its HID sub-device is missing '
            '(Windows problem code 45), so injected reports are discarded and the cursor does '
            'not move. Restart the G HUB bus device (pnputil /restart-device) or reboot Windows, '
            'then retry.')
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
        'The interface is registered by the logi_joy_xlcore upper filter when the bus '
        'device starts. Restart the bus device (pnputil /restart-device) or reboot '
        'Windows, then retry.')


def repair_driver():
    """驱动包还在系统中、设备接口却消失时，重跑一次安装以重建接口。

    针对重启后虚拟鼠标设备在设备管理器中变隐藏的场景（G HUB 的已知问题）：
    驱动文件仍在，--install 会重新注册设备接口。失败只记日志并返回 False，
    由调用方走原有的报错路径。

    Returns:
        bool: 修复后设备接口可用。
    """
    logger.warning('Virtual mouse device is missing while its driver package is still '
                   'installed (a reboot can hide it); reinstalling to recover the interface')
    try:
        install_driver(skip_copy=True)
    except VirtualMouseDriverError as exc:
        logger.warning(f'Virtual mouse driver repair failed: {exc}')
        return False
    return True


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
