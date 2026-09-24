"""Query display targets and detach a cloned target without changing the other displays."""

import ctypes
from ctypes import wintypes

QDC_ONLY_ACTIVE_PATHS = 0x2
QDC_VIRTUAL_MODE_AWARE = 0x10
DISPLAYCONFIG_PATH_SUPPORT_VIRTUAL_MODE = 0x8
DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE = 1
SDC_USE_SUPPLIED_DISPLAY_CONFIG = 0x20
SDC_VALIDATE = 0x40
SDC_APPLY = 0x80
SDC_VIRTUAL_MODE_AWARE = 0x8000
ERROR_INSUFFICIENT_BUFFER = 122


class LUID(ctypes.Structure):
    _fields_ = [('low', wintypes.DWORD), ('high', wintypes.LONG)]


class Rational(ctypes.Structure):
    _fields_ = [('numerator', wintypes.UINT), ('denominator', wintypes.UINT)]


class Region(ctypes.Structure):
    _fields_ = [('width', wintypes.UINT), ('height', wintypes.UINT)]


class VideoSignalInfo(ctypes.Structure):
    _fields_ = [
        ('pixel_rate', ctypes.c_ulonglong), ('hsync', Rational), ('vsync', Rational),
        ('active', Region), ('total', Region), ('standard', wintypes.UINT), ('scanline', wintypes.UINT),
    ]


class SourceMode(ctypes.Structure):
    _fields_ = [
        ('width', wintypes.UINT), ('height', wintypes.UINT),
        ('pixel_format', wintypes.UINT), ('position', wintypes.POINT),
    ]


class ModeData(ctypes.Union):
    # Desktop-image modes are opaque here, but must survive SetDisplayConfig unchanged.
    _fields_ = [('target', VideoSignalInfo), ('source', SourceMode), ('raw', ctypes.c_ubyte * 48)]


class ModeInfo(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', wintypes.UINT), ('id', wintypes.UINT), ('adapter', LUID), ('data', ModeData)]


class PathSource(ctypes.Structure):
    _fields_ = [
        ('adapter', LUID), ('id', wintypes.UINT), ('mode_index', wintypes.UINT), ('flags', wintypes.UINT),
    ]


class PathTarget(ctypes.Structure):
    _fields_ = [
        ('adapter', LUID), ('id', wintypes.UINT), ('mode_index', wintypes.UINT),
        ('technology', wintypes.UINT), ('rotation', wintypes.UINT), ('scaling', wintypes.UINT),
        ('refresh', Rational), ('scanline', wintypes.UINT), ('available', wintypes.BOOL), ('flags', wintypes.UINT),
    ]


class DisplayPath(ctypes.Structure):
    _fields_ = [('source', PathSource), ('target', PathTarget), ('flags', wintypes.UINT)]


class DeviceInfoHeader(ctypes.Structure):
    _fields_ = [('type', wintypes.UINT), ('size', wintypes.UINT), ('adapter', LUID), ('id', wintypes.UINT)]


class SourceDeviceName(ctypes.Structure):
    _fields_ = [('header', DeviceInfoHeader), ('name', wintypes.WCHAR * 32)]


class TargetDeviceName(ctypes.Structure):
    _fields_ = [
        ('header', DeviceInfoHeader), ('flags', wintypes.UINT), ('technology', wintypes.UINT),
        ('manufacturer', wintypes.WORD), ('product', wintypes.WORD), ('connector', wintypes.UINT),
        ('name', wintypes.WCHAR * 64), ('device_path', wintypes.WCHAR * 128),
    ]


def _user32():
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetDisplayConfigBufferSizes.argtypes = [
        wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
    ]
    api.QueryDisplayConfig.argtypes = [
        wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(DisplayPath),
        ctypes.POINTER(wintypes.UINT), ctypes.POINTER(ModeInfo), ctypes.c_void_p,
    ]
    api.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
    api.SetDisplayConfig.argtypes = [
        wintypes.UINT, ctypes.POINTER(DisplayPath), wintypes.UINT, ctypes.POINTER(ModeInfo), wintypes.UINT,
    ]
    return api


def _query(virtual_mode_aware=False):
    api = _user32()
    flags = QDC_ONLY_ACTIVE_PATHS | (QDC_VIRTUAL_MODE_AWARE if virtual_mode_aware else 0)
    for _ in range(5):
        path_count, mode_count = wintypes.UINT(), wintypes.UINT()
        result = api.GetDisplayConfigBufferSizes(flags, ctypes.byref(path_count), ctypes.byref(mode_count))
        if result:
            raise OSError(result, 'GetDisplayConfigBufferSizes failed')
        paths = (DisplayPath * path_count.value)()
        modes = (ModeInfo * mode_count.value)()
        result = api.QueryDisplayConfig(
            flags, ctypes.byref(path_count), paths, ctypes.byref(mode_count), modes, None,
        )
        if result == ERROR_INSUFFICIENT_BUFFER:
            continue
        if result:
            raise OSError(result, 'QueryDisplayConfig failed')
        # The topology can shrink between the size query and the actual query.
        return (
            (DisplayPath * path_count.value).from_buffer_copy(paths),
            (ModeInfo * mode_count.value).from_buffer_copy(modes),
        )
    raise OSError(ERROR_INSUFFICIENT_BUFFER, 'Display topology kept changing during QueryDisplayConfig')


def _source_mode_index(path):
    if path.flags & DISPLAYCONFIG_PATH_SUPPORT_VIRTUAL_MODE:
        return path.source.mode_index >> 16
    return path.source.mode_index


def _describe(paths, modes):
    api = _user32()
    displays = []
    for index, path in enumerate(paths):
        source_name, target_name = SourceDeviceName(), TargetDeviceName()
        source_name.header = DeviceInfoHeader(1, ctypes.sizeof(source_name), path.source.adapter, path.source.id)
        target_name.header = DeviceInfoHeader(2, ctypes.sizeof(target_name), path.target.adapter, path.target.id)
        for name in (source_name, target_name):
            result = api.DisplayConfigGetDeviceInfo(ctypes.byref(name))
            if result:
                raise OSError(result, 'DisplayConfigGetDeviceInfo failed')
        mode_index = _source_mode_index(path)
        if mode_index >= len(modes) or modes[mode_index].type != DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE:
            raise OSError('Active display has no valid source mode')
        mode = modes[mode_index].source
        displays.append({
            'index': index,
            'target': target_name.device_path.upper(),
            'device': source_name.name,
            'source': (path.source.adapter.high, path.source.adapter.low, path.source.id),
            'position': (mode.position.x, mode.position.y),
            'size': (mode.width, mode.height),
            'rotation': path.target.rotation,
            'refresh': (path.target.refresh.numerator, path.target.refresh.denominator),
        })
    return displays


def active_displays():
    # The legacy view exposes the shared desktop source of cross-adapter clones.
    return _describe(*_query())


def is_extended(display, displays):
    return sum(other['source'] == display['source'] for other in displays) == 1


def _apply(paths, modes, validate=False):
    flags = SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_VIRTUAL_MODE_AWARE | (SDC_VALIDATE if validate else SDC_APPLY)
    result = _user32().SetDisplayConfig(len(paths), paths, len(modes), modes, flags)
    if result:
        raise OSError(result, f'SetDisplayConfig {"validation" if validate else "apply"} failed')


def extend_display(target):
    """Detach one cloned target, preserving all other display modes and positions."""
    before = active_displays()
    display = next((item for item in before if item['target'] == target), None)
    if display is None:
        raise OSError(f'Display target disappeared: {target}')
    if is_extended(display, before):
        return

    paths, modes = _query(virtual_mode_aware=True)
    current = _describe(paths, modes)
    display = next((item for item in current if item['target'] == target), None)
    if display is None:
        raise OSError(f'Display target disappeared before extending: {target}')
    path = paths[display['index']]
    mode_index = _source_mode_index(path)
    if any(
        index != display['index'] and (
            _source_mode_index(other) == mode_index
            or (other.source.adapter.high, other.source.adapter.low, other.source.id) == display['source']
        )
        for index, other in enumerate(paths)
    ):
        raise OSError('Cannot safely detach this shared display source; set the Parsec display to Extend in Windows')

    original_modes = (ModeInfo * len(modes)).from_buffer_copy(modes)
    mode = modes[mode_index].source
    mode.position.x = max(item['position'][0] + item['size'][0] for item in current if item['target'] != target)
    mode.position.y = 0
    _apply(paths, modes, validate=True)
    _apply(paths, modes)

    try:
        after = active_displays()
        display = next((item for item in after if item['target'] == target), None)
        if display is None or not is_extended(display, after):
            raise OSError('Parsec display is still cloned after SetDisplayConfig')
        expected = {
            item['target']: (item['position'], item['size'], item['rotation'], item['refresh'])
            for item in before if item['target'] != target
        }
        actual = {
            item['target']: (item['position'], item['size'], item['rotation'], item['refresh'])
            for item in after if item['target'] != target
        }
        if actual != expected:
            raise OSError('Other display settings changed while extending the Parsec display')
    except OSError:
        _apply(paths, original_modes)
        raise
