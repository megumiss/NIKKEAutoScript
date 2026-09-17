"""G HUB 虚拟 HID 鼠标设备（驱动级）的用户态注入封装。

原理：安装 G HUB 后系统里存在一个虚拟鼠标 HID 设备。用户态进程可以直接打开它的
设备接口，用 IOCTL 0x2A2010 提交 7 字节鼠标报告，由驱动投递进系统输入栈 —— 这条
路径不经过 SendInput，因此 WH_MOUSE_LL 钩子读到的 MSLLHOOKSTRUCT.flags == 0x000000
（不带 LLMHF_INJECTED）。

本模块只做两件事：找到设备、发报告。
不安装驱动、不写注册表、不落盘、不改动 G HUB 的任何文件。
"""
import ctypes
import struct
import threading
import time
from ctypes import wintypes

from module.logger import logger

# G HUB 的虚拟鼠标设备接口 GUID。LGS 是另一套（df31f106-...，5 字节报告），不支持。
G_HUB_INTERFACE_GUID = '{1abc05c0-c378-41b9-9cef-df1aba82b015}'
DEVICE_INDEX_RANGE = range(10)


def g_hub_device_path(index):
    """G HUB 虚拟鼠标设备接口路径，index 取 0..9（本机命中 2）。

    注意：不要写成 `模板.format(index=...)` —— GUID 自带 `{...}`，会被 str.format
    当成替换字段而抛 KeyError。这里用 f-string，GUID 只在运行期代入。
    """
    return rf'\??\ROOT#SYSTEM#000{index}#{G_HUB_INTERFACE_GUID}'

IOCTL_SEND_MOUSE = 0x2A2010
REPORT_SIZE = 7
STATUS_SUCCESS = 0

GENERIC_READ_WRITE = 0xC0000000
FILE_SHARE_BOTH = 0x00000003
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

BTN_LEFT = 0x01
BTN_RIGHT = 0x02
BTN_MIDDLE = 0x04

# 光标闭环参数。请求量与实际位移非线性（实测同一请求量的实际位移会因前序运动状态
# 抖动 40% 以上），所以只做「读回 + 逼近 + 过冲收敛」。
# 逐项实测依据见 tmp/driver-validation/REPORT-move-speed-root-cause.md §8/§9。
MOVE_TOLERANCE = 2
MOVE_MAX_ITERATIONS = 400
# 单次报告上限。实测请求 160 的实际位移约 350px，20 的单步只有 ~46px，一次 800px
# 移动会被硬拆成 18 轮以上；提到 160 后同一目标的中位耗时从 78ms 降到 36ms。
MOVE_STEP_LIMIT = 160.0
# 请求下限。实测请求 1 有 2/6 概率零位移、2 仅约 1.5px，3 起稳定有 2px 且无零位移。
MOVE_STEP_MIN = 3.0
MOVE_STEP_DECAY = 0.6
# 空转恢复：请求发出后光标没动，说明请求量落在死区，放大而不是原地重试。
MOVE_STEP_RECOVER = 1.6
# 末段阻尼。实测增益（实际位移/请求量）在 1.17~2.6 之间且恒 > 1，|误差| 小于步长上限
# 时若直接 request = error 必然过冲 → 必然衰减。按 1/增益 折算请求后一次落到容差内。
MOVE_APPROACH_DAMP = 0.45
MOVE_POLL_INTERVAL = 0.004

# 滚轮：每格一个独立报告。间隔过小可能被合并，0.02 是已验证值（连续 40 格无丢格）。
WHEEL_INTERVAL = 0.02

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_ntdll = ctypes.WinDLL('ntdll')

_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL

_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_ntdll.NtDeviceIoControlFile.restype = ctypes.c_long
_ntdll.NtDeviceIoControlFile.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
]


class _IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [('Status', ctypes.c_ssize_t), ('Information', ctypes.c_size_t)]


def _clamp_int16(value):
    return max(-32768, min(32767, int(value)))


def make_report(buttons=0, dx=0, dy=0, wheel=0):
    """构造 7 字节鼠标报告。长度恒为 7。"""
    report = struct.pack(
        '<BBhhB',
        int(buttons) & 0xFF,
        0,
        _clamp_int16(dx),
        _clamp_int16(dy),
        int(wheel) & 0xFF,
    )
    assert len(report) == REPORT_SIZE
    return report


def approach_request(error, step):
    """单轴请求量：末段（|误差| 小于步长）按 MOVE_APPROACH_DAMP 折算，其余按步长截断。

    折算后仍不小于 MOVE_STEP_MIN —— 小于它的请求实测大概率零位移。
    """
    magnitude = abs(error)
    if magnitude < step:
        wish = max(MOVE_STEP_MIN, round(magnitude * MOVE_APPROACH_DAMP))
        magnitude = min(wish, magnitude)
    else:
        magnitude = step
    return int(magnitude) if error > 0 else -int(magnitude)


class LogiMouseDriver:
    """设备句柄的发现、持有与自愈。"""

    def __init__(self):
        self._handle = None
        self.device_path = None

    @property
    def opened(self):
        return self._handle is not None

    def open(self):
        """按 0000–0009 逐个尝试，用一次零报告确认 IOCTL 被接受。已打开则直接返回 True。"""
        if self.opened:
            return True
        for index in DEVICE_INDEX_RANGE:
            path = g_hub_device_path(index)
            handle = _kernel32.CreateFileW(
                path, GENERIC_READ_WRITE, FILE_SHARE_BOTH, None, OPEN_EXISTING, 0, None,
            )
            if handle == INVALID_HANDLE_VALUE:
                continue
            # 零报告语义为「无动作」，仅用于确认通道被接受
            if self._ioctl(handle, make_report()) == STATUS_SUCCESS:
                self._handle = handle
                self.device_path = path
                logger.info(f'Logitech driver device opened: {path}')
                return True
            _kernel32.CloseHandle(handle)
        logger.error('No G HUB virtual mouse interface answered IOCTL 0x2A2010')
        return False

    def close(self):
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
            self.device_path = None

    @staticmethod
    def _ioctl(handle, report):
        iosb = _IO_STATUS_BLOCK()
        status = _ntdll.NtDeviceIoControlFile(
            handle, None, None, None, ctypes.byref(iosb),
            IOCTL_SEND_MOUSE, report, len(report), None, 0,
        )
        return status & 0xFFFFFFFF

    def send(self, buttons=0, dx=0, dy=0, wheel=0):
        """提交一次报告。句柄失效（G HUB 重启 / 设备重枚举）时自动重开一次。"""
        if not self.opened and not self.open():
            return False
        report = make_report(buttons=buttons, dx=dx, dy=dy, wheel=wheel)
        if self._ioctl(self._handle, report) == STATUS_SUCCESS:
            return True
        logger.warning('Logitech driver IOCTL failed, reopening device once')
        self.close()
        if not self.open():
            return False
        return self._ioctl(self._handle, report) == STATUS_SUCCESS


class LogiMouse:
    """在 LogiMouseDriver 之上提供「光标定位 + 按键 + 滚轮」三个语义。"""

    def __init__(self, driver=None):
        self.driver = driver or LogiMouseDriver()

    # ---- 设备 ----
    def open(self):
        return self.driver.open()

    def close(self):
        self.driver.close()

    @property
    def device_path(self):
        return self.driver.device_path

    # ---- 光标 ----
    @staticmethod
    def cursor():
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    @staticmethod
    def set_cursor(x, y):
        """Shape A：直接用光标位置 API 定位。不属于本次默认路径，见实施文档 §3.1。"""
        return bool(_user32.SetCursorPos(int(x), int(y)))

    def move_rel(self, dx, dy, buttons=0):
        """Shape B 的最小单元：一次相对位移报告。"""
        return self.driver.send(buttons=buttons, dx=dx, dy=dy)

    def move_to(self, x, y, buttons=0, tolerance=None):
        """Shape B：闭环相对移动。

        请求量与实际位移非线性，所以必须「读回实际位置 → 算误差 → 逼近」。三条规则
        缺一不可，对应实测里三种失效：
        - 两轴各自持有步长：过冲按轴隔离判定。原先共用一个 step 且判定用 or，任一轴
          过冲都会把另一轴的单步一起砍掉（实测零过冲的那个轴被砍掉 78%，迭代数
          154→19 只差这一项）。
        - 末段阻尼：|误差| 小于步长上限时按 MOVE_APPROACH_DAMP 折算请求，避免
          request == error 在增益 > 1 时必然过冲 → 衰减 → 剩余长距离只能按最小步长爬。
        - 空转放大：请求发出后位置没动，说明请求量落在死区，立即放大而不是原地重试。
        """
        tolerance = MOVE_TOLERANCE if tolerance is None else tolerance
        step_x = step_y = MOVE_STEP_LIMIT
        previous = None
        previous_position = None
        for _ in range(MOVE_MAX_ITERATIONS):
            current_x, current_y = self.cursor()
            error_x, error_y = int(x) - current_x, int(y) - current_y
            if abs(error_x) <= tolerance and abs(error_y) <= tolerance:
                return True
            if previous is not None:
                if previous[0] * error_x < 0:
                    step_x = max(MOVE_STEP_MIN, step_x * MOVE_STEP_DECAY)
                if previous[1] * error_y < 0:
                    step_y = max(MOVE_STEP_MIN, step_y * MOVE_STEP_DECAY)
            if previous_position == (current_x, current_y):
                step_x = min(MOVE_STEP_LIMIT, step_x * MOVE_STEP_RECOVER)
                step_y = min(MOVE_STEP_LIMIT, step_y * MOVE_STEP_RECOVER)
            if not self.driver.send(
                buttons=buttons,
                dx=approach_request(error_x, step_x),
                dy=approach_request(error_y, step_y),
            ):
                return False
            previous = (error_x, error_y)
            previous_position = (current_x, current_y)
            # 这里必须真的等一次输入落地再读回：去掉它闭环就失去可观测性，
            # 实测 400 次迭代仍不收敛（读到的永远是上一帧位置）。
            time.sleep(MOVE_POLL_INTERVAL)
        return False

    # ---- 按键（按钮字段是状态，不是边沿事件） ----
    def press(self, buttons=BTN_LEFT):
        return self.driver.send(buttons=buttons)

    def release(self):
        return self.driver.send(buttons=0)

    # ---- 滚轮 ----
    def wheel(self, notches):
        """notches > 0 → 内容下移；< 0 → 内容上移。每格一个独立报告。"""
        notches = int(notches)
        if notches == 0:
            return True
        direction = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            if not self.driver.send(wheel=direction):
                return False
            time.sleep(WHEEL_INTERVAL)
        return True


# 驱动对象是进程级共享的：Device.__init__ 在 GameNotRunningError 时会重试构造
# （device.py:32-46），每次重试都会走一遍 Automation._init_input()。共享同一个
# LogiMouse 可以避免重复打开设备句柄。
_shared_lock = threading.RLock()
_shared_mouse = None


def shared_mouse():
    global _shared_mouse
    with _shared_lock:
        if _shared_mouse is None:
            _shared_mouse = LogiMouse()
        return _shared_mouse
