import ctypes
import time

import numpy as np
import pyautogui
from pynput.mouse import Button, Controller

from module.logger import logger

# SendInput 相关常量
_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008

# 需要用 EXTENDEDKEY 标志发送的扩展键（方向键等）
_EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}

_KEY_NAME_TO_VK = {
    'backspace': 0x08,
    'tab': 0x09,
    'enter': 0x0D,
    'shift': 0x10,
    'ctrl': 0x11,
    'alt': 0x12,
    'esc': 0x1B,
    'escape': 0x1B,
    'space': 0x20,
    'pageup': 0x21,
    'pagedown': 0x22,
    'end': 0x23,
    'home': 0x24,
    'left': 0x25,
    'up': 0x26,
    'right': 0x27,
    'down': 0x28,
    'insert': 0x2D,
    'delete': 0x2E,
}
_KEY_NAME_TO_VK.update({f'f{index}': 0x6F + index for index in range(1, 25)})


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', ctypes.c_ushort),
        ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong),
        ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', ctypes.c_long),
        ('dy', ctypes.c_long),
        ('mouseData', ctypes.c_ulong),
        ('dwFlags', ctypes.c_ulong),
        ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', ctypes.c_ulong),
        ('wParamL', ctypes.c_short),
        ('wParamH', ctypes.c_ushort),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [('ki', _KEYBDINPUT), ('mi', _MOUSEINPUT), ('hi', _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('ii', _INPUT_UNION)]


_VK_SHIFT = 0x10


def _key_name_to_vk(key):
    """将按键名转换为虚拟键码，返回 (vk, need_shift)"""
    if len(key) == 1:
        vk_scan = ctypes.windll.user32.VkKeyScanW(ctypes.c_wchar(key))
        vk = vk_scan & 0xFF
        if vk_scan != -1 and vk != 0xFF:
            # 高字节为修饰键状态：1=Shift（大写字母、@! 等符号需要）
            return vk, bool((vk_scan >> 8) & 1)
        raise ValueError(f'无法识别的按键: {key}')
    vk = _KEY_NAME_TO_VK.get(key.lower())
    if vk is None:
        raise ValueError(f'无法识别的按键: {key}')
    return vk, False


def _send_scan_key(vk, key_up=False):
    """通过 SendInput 以扫描码方式发送按键，兼容读取 DirectInput 的游戏窗口"""
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    flags = _KEYEVENTF_SCANCODE
    if vk in _EXTENDED_VK:
        flags |= _KEYEVENTF_EXTENDEDKEY
    if key_up:
        flags |= _KEYEVENTF_KEYUP

    extra = ctypes.c_ulong(0)
    ii = _INPUT_UNION()
    ii.ki = _KEYBDINPUT(0, scan, flags, 0, ctypes.pointer(extra))
    x = _INPUT(_INPUT_KEYBOARD, ii)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


class Input:
    # 禁用pyautogui的失败安全特性，防止意外中断
    pyautogui.FAILSAFE = False

    def mouse_click(self, x, y):
        """在屏幕上的（x，y）位置执行鼠标点击操作"""
        try:
            pyautogui.click(x, y)
            logger.debug(f'鼠标点击 ({x}, {y})')
        except Exception as e:
            logger.error(f'鼠标点击出错：{e}')

    def press_mouse_click(self, x, y, wait_time=0.2):
        """模拟鼠标左键的点击操作，可以指定按下的时间"""
        try:
            pyautogui.mouseDown(x, y)
            time.sleep(wait_time)
            pyautogui.mouseUp()
            logger.debug(f'按下鼠标左键 ({x}, {y})')
        except Exception as e:
            logger.error(f'按下鼠标左键出错：{e}')

    def mouse_down(self, x, y):
        """在屏幕上的（x，y）位置按下鼠标按钮"""
        try:
            pyautogui.mouseDown(x, y)
            logger.debug(f'鼠标按下 ({x}, {y})')
        except Exception as e:
            logger.error(f'鼠标按下出错：{e}')

    def mouse_up(self):
        """释放鼠标按钮"""
        try:
            pyautogui.mouseUp()
            logger.debug('鼠标释放')
        except Exception as e:
            logger.error(f'鼠标释放出错：{e}')

    def mouse_move(self, x, y):
        """将鼠标光标移动到屏幕上的（x，y）位置"""
        try:
            pyautogui.moveTo(x, y)
            logger.debug(f'鼠标移动 ({x}, {y})')
        except Exception as e:
            logger.error(f'鼠标移动出错：{e}')

    def mouse_scroll(self, count, direction=-1, pause=True):
        """滚动鼠标滚轮，方向和次数由参数指定"""
        for _ in range(count):
            pyautogui.scroll(direction, _pause=pause)
        logger.debug(f'滚轮滚动 {count * direction} 次')

    def press_key(self, key, wait_time=0.2):
        """模拟键盘按键，可以指定按下的时间"""
        try:
            pyautogui.keyDown(key)
            time.sleep(wait_time)  # 等待指定的时间
            pyautogui.keyUp(key)
            logger.debug(f'键盘按下 {key}')
        except Exception as e:
            logger.error(f'键盘按下 {key} 出错：{e}')

    def secretly_press_key(self, key, wait_time=0.2):
        """(不输出具体键位)模拟键盘按键，可以指定按下的时间"""
        try:
            vk, need_shift = _key_name_to_vk(key)
            if need_shift:
                _send_scan_key(_VK_SHIFT)
            _send_scan_key(vk)
            time.sleep(wait_time)  # 等待指定的时间
            _send_scan_key(vk, key_up=True)
            if need_shift:
                _send_scan_key(_VK_SHIFT, key_up=True)
            logger.debug('键盘按下 *')
        except Exception as e:
            logger.error(f'键盘按下 * 出错：{e}')

    def press_mouse(self, wait_time=0.2):
        """模拟鼠标左键的点击操作，可以指定按下的时间"""
        try:
            pyautogui.mouseDown()
            time.sleep(wait_time)  # 等待指定的时间
            pyautogui.mouseUp()
            logger.debug('按下鼠标左键')
        except Exception as e:
            logger.error(f'按下鼠标左键出错：{e}')

    def __init__(self):
        self.mouse = Controller()

    def mouse_swipe(self, p1, p2, speed=1.0):
        """
        使用 pynput 实现自然流畅滑动
        speed: 数值越大越快
        """
        distance = np.linalg.norm(np.array(p2) - np.array(p1))

        # 短距离滑动也必须至少生成一个移动段，避免滚动改走滑动后除零。
        segments = max(1, int(distance / 20))
        total_time = max(0.05, min(distance / (100 * speed), 0.15))
        step_delay = total_time / segments

        self.mouse.position = (p1[0], p1[1])
        time.sleep(0.01)
        self.mouse.press(Button.left)

        for i in range(1, segments + 1):
            t = i / segments
            x = p1[0] + (p2[0] - p1[0]) * t
            y = p1[1] + (p2[1] - p1[1]) * t
            self.mouse.position = (x, y)
            time.sleep(step_delay)

        self.mouse.release(Button.left)


def insert_swipe(p0, p3, speed=15, min_distance=10):
    """
    Insert way point from start to end.
    First generate a cubic bézier curve

    Args:
        p0: Start point.
        p3: End point.
        speed: Average move speed, pixels per 10ms.
        min_distance:

    Returns:
        list[list[int]]: List of points.

    Examples:
        > insert_swipe((400, 400), (600, 600), speed=20)
        [[400, 400], [406, 406], [416, 415], [429, 428], [444, 442], [462, 459], [481, 478], [504, 500], [527, 522],
        [545, 540], [560, 557], [573, 570], [584, 582], [592, 590], [597, 596], [600, 600]]
    """
    p0 = np.array(p0)
    p3 = np.array(p3)

    # Random control points in Bézier curve
    distance = np.linalg.norm(p3 - p0)
    p1 = 2 / 3 * p0 + 1 / 3 * p3 + random_theta() * random_rho(distance * 0.1)
    p2 = 1 / 3 * p0 + 2 / 3 * p3 + random_theta() * random_rho(distance * 0.1)

    # Random `t` on Bézier curve, sparse in the middle, dense at start and end
    segments = max(int(distance / speed) + 1, 5)
    lower = random_normal_distribution(-85, -60)
    upper = random_normal_distribution(80, 90)
    theta = np.arange(lower + 0.0, upper + 0.0001, (upper - lower) / segments)
    ts = np.sin(theta / 180 * np.pi)
    ts = np.sign(ts) * abs(ts) ** 0.9
    ts = (ts - min(ts)) / (max(ts) - min(ts))

    # Generate cubic Bézier curve
    points = []
    prev = (-100, -100)
    for t in ts:
        point = p0 * (1 - t) ** 3 + 3 * p1 * t * (1 - t) ** 2 + 3 * p2 * t**2 * (1 - t) + p3 * t**3
        point = point.astype(int).tolist()
        if np.linalg.norm(np.subtract(point, prev)) < min_distance:
            continue

        points.append(point)
        prev = point

    # Delete nearing points
    if len(points[1:]):
        distance = np.linalg.norm(np.subtract(points[1:], points[0]), axis=1)
        mask = np.append(True, distance > min_distance)
        points = np.array(points)[mask].tolist()
    else:
        points = [p0, p3]
    print(points)

    return points


def random_normal_distribution(a, b, n=5):
    output = np.mean(np.random.uniform(a, b, size=n))
    return output


def random_theta():
    theta = np.random.uniform(0, 2 * np.pi)
    return np.array([np.sin(theta), np.cos(theta)])


def random_rho(dis):
    return random_normal_distribution(-dis, dis)
