import time

import numpy as np
import pyautogui
from pynput.mouse import Button, Controller

from module.logger import logger


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
            pyautogui.write
            pyautogui.keyDown(key)
            time.sleep(wait_time)  # 等待指定的时间
            pyautogui.keyUp(key)
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

        segments = max(10, int(distance / 50))
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
