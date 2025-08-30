import math
import random
import time

import numpy as np
import pyautogui

from module.base.utils import ensure_int, point2str
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
            logger.debug('按下鼠标左键')
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

    def mouse_swipe(self, p1, p2, speed=15, hold=0, min_distance=5):
        """竖向滑动操作，使用优化后的插值方法，加入惯性效果"""
        points = insert_swipe(p0=p1, p3=p2, speed=speed, min_distance=min_distance)

        if len(points) < 2:
            logger.error("生成的滑动路径点少于2个，无法进行滑动操作！")
            return  # 或者抛出异常

        # Starting the drag from the first point
        pyautogui.moveTo(points[0][0], points[0][1])
        pyautogui.mouseDown()

        # Moving through the points with easing
        for i, point in enumerate(points[1:-1]):
            # 计算逐步减速的时间，根据路径的顺序减慢速度
            # Ease-out: t^2
            t = (i + 1) / len(points)  # 计算当前点的时间进度
            duration = speed * (1 - (t * t)) / 1000  # 减速效果
            pyautogui.moveTo(point[0], point[1], duration=duration)  # duration is in seconds
            time.sleep(0.01)

        # Final move to last point
        pyautogui.moveTo(points[-1][0], points[-1][1], duration=speed / 1000)

        # Optionally hold the mouse
        if hold:
            time.sleep(hold)

        # Release the mouse
        pyautogui.mouseUp()

def insert_swipe(p0, p3, speed=15, min_distance=5):
    """
    插入通过线性插值优化的滑动路径点，并模拟惯性效果
    """
    p0 = np.array(p0)
    p3 = np.array(p3)

    # 计算起始和目标点之间的距离
    distance = np.linalg.norm(p3 - p0)
    segments = max(int(distance / speed) + 1, 5)

    # 使用线性插值计算路径点
    points = []
    for t in np.linspace(0, 1, segments):
        point = p0 * (1 - t) + p3 * t
        point = point.astype(int).tolist()
        if len(points) > 0 and np.linalg.norm(np.subtract(point, points[-1])) > min_distance:
            points.append(point)

    # 确保至少有两个路径点
    if len(points) < 2:
        points = [p0.tolist(), p3.tolist()]

    return points


def random_normal_distribution(a, b, n=5):
    output = np.mean(np.random.uniform(a, b, size=n))
    return output


def random_theta():
    theta = np.random.uniform(0, 2 * np.pi)
    return np.array([np.sin(theta), np.cos(theta)])


def random_rho(dis):
    return random_normal_distribution(-dis, dis)
