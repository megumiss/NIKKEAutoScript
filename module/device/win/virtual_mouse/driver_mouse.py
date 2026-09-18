"""虚拟鼠标 HID 设备（驱动级）的用户态注入封装。

原理：安装虚拟鼠标驱动后系统里会存在一个虚拟鼠标 HID 设备。用户态进程可以直接打开它的
设备接口，用 IOCTL 0x2A2010 提交 7 字节鼠标报告，由驱动投递进系统输入栈 —— 这条
路径不经过 SendInput，因此 WH_MOUSE_LL 钩子读到的 MSLLHOOKSTRUCT.flags == 0x000000
（不带 LLMHF_INJECTED）。

本模块只做两件事：找到设备、发报告。
本模块只负责找到设备并发送报告：不安装驱动、不写注册表、不落盘。
"""
import ctypes
import struct
import threading
import time
from ctypes import wintypes

from module.logger import logger
from module.tools.virtual_mouse_driver import enum_interface_paths

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

# 拖动流参数。拖动不需要每个路点的落点精度，所以不做「到达即停」的收敛，而是在整个
# duration 内匀速推进 —— 位移曲线连续，游戏读到的才是平滑拖动。
# 增益实测随请求量变化（请求 8 约 11.5px、请求 20 约 46px），所以按实测比值在线估计。
DRAG_GAIN_INIT = 1.5
DRAG_GAIN_SMOOTH = 0.4
DRAG_GAIN_RANGE = (0.2, 5.0)
DRAG_GAIN_FLOOR = 0.2
# 单报告请求上下限。下限与 MOVE_STEP_MIN 同源：更小的请求会被整数截断成零位移。
DRAG_MIN_REPORT = 3.0
DRAG_MAX_REPORT = 60.0
# 单报告期望位移上限。末段剩余误差会全部压到最后一个报告上，不夹住会出现可见跳变。
DRAG_MAX_WANT = 30.0

# 定时器粒度。默认 15.62ms 会把 sleep 向上量到整刻度：实测 sleep(4ms) 实际 15.46ms、
# sleep(20ms) 实际 30.95ms。闭环每轮多等 11ms、拖动节拍被量化成 15.6/31ms，表现为
# 「一卡一卡」且手势时长成倍拉长。请求 0.5ms 后 sleep(4ms) 实测 4.01ms。
# 逐项实测见 tmp/driver-validation/timer_granularity_probe.py。
TIMER_RESOLUTION_100NS = 5000

# 滚轮：每格一个独立报告。间隔过小可能被合并，0.02 是已验证值（连续 40 格无丢格）。
WHEEL_INTERVAL = 0.02

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_ntdll = ctypes.WinDLL('ntdll')
_winmm = ctypes.WinDLL('winmm')

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
_ntdll.NtSetTimerResolution.restype = ctypes.c_long
_ntdll.NtSetTimerResolution.argtypes = [
    wintypes.ULONG, ctypes.c_int, ctypes.POINTER(wintypes.ULONG),
]

_timer_resolution_held = False


def _raise_timer_resolution():
    """把定时器粒度提到 0.5ms，否则 time.sleep 会被量到 15.62ms 整刻度。

    进程内幂等。这是系统级计时器请求（同时影响其它进程的 Sleep 精度），
    因此驱动通道关闭时会还原，不长期占用。
    """
    global _timer_resolution_held
    if _timer_resolution_held:
        return
    actual = wintypes.ULONG()
    if _ntdll.NtSetTimerResolution(TIMER_RESOLUTION_100NS, 1, ctypes.byref(actual)) == 0:
        _timer_resolution_held = True
        logger.info(f'Timer resolution raised to {actual.value / 10000:.2f}ms')
        return
    # 回退：winmm 的 timeBeginPeriod，粒度 1ms，比 15.62ms 已经足够
    if _winmm.timeBeginPeriod(1) == 0:
        _timer_resolution_held = True
        logger.info('Timer resolution raised to 1ms via timeBeginPeriod')
        return
    logger.warning('Could not raise timer resolution, input pacing will be quantized')


def _restore_timer_resolution():
    global _timer_resolution_held
    if not _timer_resolution_held:
        return
    actual = wintypes.ULONG()
    _ntdll.NtSetTimerResolution(TIMER_RESOLUTION_100NS, 0, ctypes.byref(actual))
    _winmm.timeEndPeriod(1)
    _timer_resolution_held = False


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


def _drag_want(remaining_error, remaining_reports):
    """本轮期望位移：剩余误差按剩余次数平摊，再夹到 DRAG_MAX_WANT。

    平摊本身就是比例修正 —— 落后了剩余误差变大、请求随之变大；冲过了符号翻转、请求反向。
    夹上限是为了不让末段的修正量集中到最后一个报告上（那会变成一次可见跳变）。
    """
    want = remaining_error / remaining_reports
    return max(-DRAG_MAX_WANT, min(DRAG_MAX_WANT, want))


def _drag_request(want, gain):
    """把期望位移换算成请求量：请求 = 期望 / 增益估计，夹在 [DRAG_MIN_REPORT, DRAG_MAX_REPORT]。"""
    if abs(want) < 0.5:
        return 0
    magnitude = abs(want) / max(gain, DRAG_GAIN_FLOOR)
    magnitude = min(DRAG_MAX_REPORT, max(DRAG_MIN_REPORT, magnitude))
    return int(magnitude) if want > 0 else -int(magnitude)


def _update_gain(gain, request, measured):
    """用「实测位移 / 请求量」在线更新增益估计。

    请求量低于可动下限时比值不可信（实测请求 1 有 2/6 概率零位移），跳过不更新。
    """
    if abs(request) < DRAG_MIN_REPORT:
        return gain
    ratio = measured / request
    if not DRAG_GAIN_RANGE[0] <= ratio <= DRAG_GAIN_RANGE[1]:
        return gain
    return gain + (ratio - gain) * DRAG_GAIN_SMOOTH


class VirtualMouseDevice:
    """设备句柄的发现、持有与自愈。"""

    def __init__(self):
        self._handle = None
        self.device_path = None

    @property
    def opened(self):
        return self._handle is not None

    def open(self):
        """枚举驱动注册的设备接口，用一次零报告确认 IOCTL 被接受。已打开则直接返回 True。

        路径来自 SetupAPI 枚举（与 module/tools/virtual_mouse_driver 的发现逻辑同源），
        不按 ROOT#SYSTEM#000N 猜序号。
        """
        if self.opened:
            return True
        for path in enum_interface_paths():
            handle = _kernel32.CreateFileW(
                path, GENERIC_READ_WRITE, FILE_SHARE_BOTH, None, OPEN_EXISTING, 0, None,
            )
            if handle == INVALID_HANDLE_VALUE:
                continue
            # 零报告语义为「无动作」，仅用于确认通道被接受
            if self._ioctl(handle, make_report()) == STATUS_SUCCESS:
                self._handle = handle
                self.device_path = path
                _raise_timer_resolution()
                logger.info(f'Virtual mouse device opened: {path}')
                return True
            _kernel32.CloseHandle(handle)
        logger.error('No virtual mouse interface answered IOCTL 0x2A2010')
        return False

    def close(self):
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
            self.device_path = None
        _restore_timer_resolution()

    @staticmethod
    def _ioctl(handle, report):
        iosb = _IO_STATUS_BLOCK()
        status = _ntdll.NtDeviceIoControlFile(
            handle, None, None, None, ctypes.byref(iosb),
            IOCTL_SEND_MOUSE, report, len(report), None, 0,
        )
        return status & 0xFFFFFFFF

    def send(self, buttons=0, dx=0, dy=0, wheel=0):
        """提交一次报告。句柄失效（驱动重启 / 设备重枚举）时自动重开一次。"""
        if not self.opened and not self.open():
            return False
        report = make_report(buttons=buttons, dx=dx, dy=dy, wheel=wheel)
        if self._ioctl(self._handle, report) == STATUS_SUCCESS:
            return True
        logger.warning('Virtual mouse IOCTL failed, reopening device once')
        self.close()
        if not self.open():
            return False
        return self._ioctl(self._handle, report) == STATUS_SUCCESS


class VirtualMouse:
    """在 VirtualMouseDevice 之上提供「光标定位 + 按键 + 滚轮」三个语义。"""

    def __init__(self, driver=None):
        self.driver = driver or VirtualMouseDevice()

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

    def drag_stream(self, x, y, steps, interval, buttons=BTN_LEFT):
        """拖动流：按固定节拍把剩余误差平摊成 steps 次相对报告，边发边修正增益估计。

        与 move_to 的区别是「不追求每步落点」：拖动过程中光标在哪一点无所谓，只要位移
        曲线连续、总位移正确。所以这里不做「到达即停」的收敛 —— 逐路点闭环会让每个路点
        都停下来收敛再等一个固定间隔，游戏读到的就是一段段突进加整帧静止。
        实测 300px 手势：逐路点闭环 767ms / 停顿 ≥20ms 七次 / 每帧位移 p10 = 0；
        流式 100ms（= 名义时长）/ 零停顿 / p10 = 48px。
        """
        start = time.perf_counter()
        position = self.cursor()
        previous_position = position
        previous_request = None
        gain_x = gain_y = DRAG_GAIN_INIT
        for index in range(steps):
            if previous_request is not None:
                gain_x = _update_gain(
                    gain_x, previous_request[0], position[0] - previous_position[0])
                gain_y = _update_gain(
                    gain_y, previous_request[1], position[1] - previous_position[1])
            previous_position = position
            remaining = steps - index
            request_x = _drag_request(_drag_want(x - position[0], remaining), gain_x)
            request_y = _drag_request(_drag_want(y - position[1], remaining), gain_y)
            if not self.driver.send(buttons=buttons, dx=request_x, dy=request_y):
                return False
            previous_request = (request_x, request_y)
            # 用绝对截止时刻推进，避免「发送 + 读回」的开销逐轮累积成节拍漂移
            delay = start + (index + 1) * interval - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            position = self.cursor()
        return True

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
# VirtualMouse 可以避免重复打开设备句柄。
_shared_lock = threading.RLock()
_shared_mouse = None


def shared_mouse():
    global _shared_mouse
    with _shared_lock:
        if _shared_mouse is None:
            _shared_mouse = VirtualMouse()
        return _shared_mouse
