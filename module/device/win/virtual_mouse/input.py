"""VirtualMouseInput：虚拟鼠标驱动级鼠标方案（PCClientInfo.ControlScheme == 'driver'）。

只替换 Input 的 8 个鼠标原语，其余（键盘、insert_swipe）全部继承。
业务层 click_xy / appear_then_click / ensure_sroll / ui_ensure 零改动。

坐标约定与 Input 完全一致：方法入参为屏幕绝对坐标（Automation 已叠加窗口 offset）。
"""
import ctypes
import threading
import time

from module.device.win.input import Input
from module.device.win.virtual_mouse.driver_mouse import (
    BTN_LEFT,
    WHEEL_INTERVAL,
    shared_mouse,
)
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.tools.virtual_mouse_driver import driver_package_present, repair_driver, sub_device_present

# 跨进程互斥体。Local\ 前缀：非管理员即可创建，作用域为当前登录会话。
SCHEME_MUTEX_NAME = 'Local\\NKAS.DriverControlScheme'
ERROR_ALREADY_EXISTS = 183

# 单击按下时长。实测 0.06~0.09s 区间有效（等价于 2~5 帧 @60fps），取 0.09 与人类点击一致。
CLICK_HOLD = 0.09
# 拖动参数。手势总时长 = min(distance / (100 * speed), DRAG_MAX_DURATION)，再均分成
# duration / DRAG_REPORT_INTERVAL 份相对位移报告按等间隔发出。
DRAG_REPORT_INTERVAL = 0.004
DRAG_MIN_STEPS = 8
DRAG_MAX_DURATION = 0.6
DRAG_SETTLE_DELAY = 0.06
# 连续失败达到该次数即中止，绝不静默降级到 SendInput
FAILURE_LIMIT = 3

_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

_claim_lock = threading.RLock()
_scheme_mutex = None


def claim_scheme_mutex():
    """认领驱动通道的跨进程互斥体。

    幂等：本进程已认领则直接返回 True。这一点是必需的 —— Device.__init__ 在
    GameNotRunningError 时会重试构造（device.py:32-46），每次重试都会新建一个
    VirtualMouseInput；若每次都去 CreateMutexW，同进程的第二次创建同样会得到
    ERROR_ALREADY_EXISTS，会被误判成「另一个实例在占用」。
    """
    global _scheme_mutex
    with _claim_lock:
        if _scheme_mutex is not None:
            return True
        handle = _kernel32.CreateMutexW(None, True, SCHEME_MUTEX_NAME)
        if not handle:
            logger.error(f'CreateMutexW failed for {SCHEME_MUTEX_NAME}')
            return False
        # 必须紧接 CreateMutexW 读取，中间不能插入其它 API 调用
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            return False
        _scheme_mutex = handle
        return True


class VirtualMouseInput(Input):
    # Shape B（驱动闭环）是已验证路径，保持为默认值。
    # 'cursor'（Shape A：SetCursorPos 直定位）未验证，仅作为可选路径保留。
    MOVE_BACKEND = 'driver'

    def __init__(self, config_name=None):
        """
        Args:
            config_name: 实例名，仅用于日志与报错提示，可为 None
        """
        super().__init__()
        self._config_name = config_name or 'nkas'
        self._lock = threading.RLock()
        self._failures = 0
        self.mouse_driver = shared_mouse()
        self._preflight()

    # ------------------------------------------------------------------
    # 启动预检：两道闸门，失败即显式中止（设备缺失时先尝试修复）
    # ------------------------------------------------------------------
    def _preflight(self):
        if not claim_scheme_mutex():
            logger.error(
                f'Control scheme driver is already in use by another NKAS instance '
                f'(mutex {SCHEME_MUTEX_NAME}). The driver channel drives the single global '
                f'physical cursor, so two instances would fight over it. '
                f'Switch the other instance back to postmessage.'
            )
            raise RequestHumanTakeover
        if self._channel_ready():
            return
        # 重启后虚拟鼠标设备可能在设备管理器中变隐藏（G HUB 已知问题）：驱动包还在
        # 但接口没注册或 HID 子设备未呈现，重跑一次安装即可重建，修复成功则重试
        if driver_package_present() and repair_driver() and self._channel_ready():
            return
        if sub_device_present() is False:
            logger.error(
                'Control scheme driver: the virtual mouse interface exists but its HID '
                'sub-device is not present (Windows problem code 45), so injected reports are '
                'discarded and the cursor never moves. Restart the G HUB bus device '
                '(pnputil /restart-device) or reboot Windows, then retry.'
            )
        else:
            logger.error(
                'Control scheme driver requires the virtual mouse driver to be installed, which '
                'provides the virtual HID mouse device (GUID 1abc05c0-...). '
                'No interface answered IOCTL 0x2A2010.'
            )
        raise RequestHumanTakeover

    def _channel_ready(self):
        """接口能打开且 HID 子设备实际呈现。

        幽灵设备（代码 45）下接口照样能打开、IOCTL 也返回成功，只测 open() 会把它误判成
        可用；子设备判不出来（None）时按可用处理，不改变既有行为。
        """
        return self.mouse_driver.open() and sub_device_present() is not False

    # ------------------------------------------------------------------
    # 失败处理：显式报错，绝不静默降级
    # ------------------------------------------------------------------
    def _checked(self, ok, what):
        if ok:
            self._failures = 0
            return True
        self._failures += 1
        logger.error(f'Virtual mouse driver {what} failed ({self._failures}/{FAILURE_LIMIT})')
        if self._failures >= FAILURE_LIMIT:
            logger.critical(
                'Virtual mouse driver channel is no longer usable. '
                'Stop instead of silently falling back to SendInput.'
            )
            raise RequestHumanTakeover
        return False

    # ------------------------------------------------------------------
    # 光标
    # ------------------------------------------------------------------
    def _move_to(self, x, y, buttons=0):
        if self.MOVE_BACKEND == 'cursor' and not buttons:
            return self.mouse_driver.set_cursor(x, y)
        return self.mouse_driver.move_to(x, y, buttons=buttons)

    def mouse_move(self, x, y):
        with self._lock:
            if not self._checked(self._move_to(x, y), f'move ({int(x)}, {int(y)})'):
                return
            logger.debug(f'Virtual mouse move ({int(x)}, {int(y)})')

    # ------------------------------------------------------------------
    # 点击 / 长按
    # ------------------------------------------------------------------
    def _press_hold_release(self, hold_time):
        """按下 → 保持 → 抬起。抬起失败必须补发，避免按键卡在按下态。"""
        if not self.mouse_driver.press(BTN_LEFT):
            self._checked(False, 'left button down')
            return False
        try:
            time.sleep(hold_time)
        finally:
            released = self.mouse_driver.release()
            if not released:
                self.mouse_driver.release()
        return self._checked(released, 'left button up')

    def mouse_click(self, x, y):
        with self._lock:
            if not self._move_to(x, y):
                self._checked(False, f'move ({int(x)}, {int(y)})')
                return
            # 一次完整操作成功后才清零，避免定位成功掩盖连续按键失败。
            if not self._press_hold_release(CLICK_HOLD):
                return
            logger.debug(f'Virtual mouse click ({int(x)}, {int(y)})')

    def press_mouse_click(self, x, y, wait_time=0.2):
        with self._lock:
            if not self._move_to(x, y):
                self._checked(False, f'move ({int(x)}, {int(y)})')
                return
            if not self._press_hold_release(wait_time):
                return
            logger.debug(f'Virtual mouse press {wait_time}s ({int(x)}, {int(y)})')

    def mouse_down(self, x, y):
        with self._lock:
            if not self._move_to(x, y):
                self._checked(False, f'move ({int(x)}, {int(y)})')
                return
            self._checked(self.mouse_driver.press(BTN_LEFT), f'button down ({int(x)}, {int(y)})')

    def mouse_up(self):
        with self._lock:
            self._checked(self.mouse_driver.release(), 'button up')

    def press_mouse(self, wait_time=0.2):
        """在当前位置按下并保持。

        签名与 Input.press_mouse 一致，不接收坐标。
        """
        with self._lock:
            self._press_hold_release(wait_time)

    # ------------------------------------------------------------------
    # 滚轮
    # ------------------------------------------------------------------
    def mouse_scroll(self, count, direction=-1, pause=True):
        """pause 参数只为签名兼容而保留，驱动路径用 WHEEL_INTERVAL 内部节拍。"""
        count = int(count)
        if count <= 0:
            return
        with self._lock:
            if not self._checked(
                self.mouse_driver.wheel(int(direction) * count),
                f'wheel {direction} x {count}',
            ):
                return
            logger.debug(f'Virtual mouse wheel {count * int(direction)} 格')

    # ------------------------------------------------------------------
    # 拖拽
    # ------------------------------------------------------------------
    def mouse_swipe(self, p1, p2, speed=1.0):
        x1, y1 = int(p1[0]), int(p1[1])
        x2, y2 = int(p2[0]), int(p2[1])
        distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if distance < 1:
            return
        duration = max(0.05, min(distance / (100 * speed), DRAG_MAX_DURATION))
        steps = max(DRAG_MIN_STEPS, round(duration / DRAG_REPORT_INTERVAL))
        interval = duration / steps

        with self._lock:
            if not self._move_to(x1, y1):
                self._checked(False, f'drag start ({x1}, {y1})')
                return
            if not self.mouse_driver.press(BTN_LEFT):
                self._checked(False, f'drag down ({x1}, {y1})')
                return
            try:
                ok = self.mouse_driver.drag_stream(x2, y2, steps, interval, buttons=BTN_LEFT)
            finally:
                # 无论中途如何退出，都必须把左键还回去，避免按键卡在按下态
                if not self.mouse_driver.release():
                    self.mouse_driver.release()
                    ok = False
            if not self._checked(ok, f'drag ({x1}, {y1}) -> ({x2}, {y2})'):
                return
            time.sleep(DRAG_SETTLE_DELAY)
        logger.debug(f'Virtual mouse drag ({x1}, {y1}) -> ({x2}, {y2}), {duration:.2f}s / {steps} 段')
