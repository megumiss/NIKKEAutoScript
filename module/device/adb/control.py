from functools import cached_property

import numpy as np

from module.base.button import Button
from module.base.utils import ensure_int, point2str
from module.device.adb.method.maatouch import MaaTouch
from module.device.adb.method.minitouch import Minitouch
from module.logger import logger


class Control(Minitouch, MaaTouch):
    def handle_control_check(self, button):
        # Will be overridden in Device
        pass

    @cached_property
    def click_methods(self):
        return {
            'minitouch': self.click_minitouch,
            'uiautomator2': self.click_uiautomator2,
            'ADB': self.click_adb,
            'MaaTouch': self.click_maatouch,
        }

    def click_xy(self, x, y):
        """
        坐标级点击入口（业务层直接拿坐标时用），按 Emulator_ControlMethod 分发。
        """
        method = self.click_methods.get(
            self.config.Emulator_ControlMethod)
        method(x, y)

    def long_click_xy(self, x, y, duration=1.0):
        """坐标级长按入口，按 Emulator_ControlMethod 分发"""
        method = self.config.Emulator_ControlMethod
        if method == 'uiautomator2':
            self.long_click_uiautomator2(x, y, duration=duration)
        elif method == 'ADB':
            self.swipe_adb((x, y), (x, y), duration=duration)
        elif method == 'MaaTouch':
            self.long_click_maatouch(x, y, duration=duration)
        else:
            self.long_click_minitouch(x, y, duration=duration)

    def drag_xy(self, p1, p2):
        """坐标级拖拽入口，按 Emulator_ControlMethod 分发"""
        method = self.config.Emulator_ControlMethod
        if method == 'uiautomator2':
            # 与原 minitouch 实现语义接近：5s 内从起点拖到终点
            self.u2.drag(*p1, *p2, duration=5)
        elif method == 'ADB':
            self.swipe_adb(p1, p2, duration=5)
        elif method == 'MaaTouch':
            self.drag_maatouch(p1, p2)
        else:
            self.drag_minitouch(p1, p2)

    def click(self, button: Button, click_offset=0, control_check=True):
        """Method to click a button.

        Args:
            button (button.Button): AzurLane Button instance.
            control_check (bool):
        """
        if control_check:
            self.handle_control_check(button)

        # x, y = random_rectangle_point(button.button)
        x, y = button.location
        # 如果 click_offset 是单个数字，代表 x 和 y 都偏移同样的量
        if isinstance(click_offset, (int, float)):
            x += click_offset
            y += click_offset
        # 如果是 (offset_x, offset_y) 形式，分别偏移
        elif isinstance(click_offset, (tuple, list)) and len(click_offset) == 2:
            x += click_offset[0]
            y += click_offset[1]

        x, y = ensure_int(x, y)
        logger.info(
            'Click %s @ %s' % (point2str(x, y), button)
        )
        method = self.click_methods.get(
            self.config.Emulator_ControlMethod)
        method(x, y)

    def swipe(self, p1, p2, speed=15, method='swipe', name='SWIPE',
            distance_check=True, handle_control_check=True):
        if handle_control_check:
            self.handle_control_check(name)
        p1, p2 = ensure_int(p1, p2)
        # method = self.config.Emulator_ControlMethod
        # if method == 'minitouch':
        logger.info('%s %s -> %s' % (method, point2str(*p1), point2str(*p2)))

        if distance_check:
            if np.linalg.norm(np.subtract(p1, p2)) < 10:
                # Should swipe a certain distance, otherwise AL will treat it as click.
                # uiautomator2 should >= 6px, minitouch should >= 5px
                logger.info('Swipe distance < 10px, dropped')
                return

        # 控制方案在这里统一分发（业务层的滑动都走 device.swipe）
        control = self.config.Emulator_ControlMethod
        if control == 'uiautomator2':
            self.swipe_uiautomator2(p1, p2, duration=1.0 if method == 'scroll' else 0.1)
        elif control == 'ADB':
            self.swipe_adb(p1, p2, duration=1.0 if method == 'scroll' else 0.1)
        elif control == 'MaaTouch':
            self.swipe_maatouch(p1, p2)
        else:
            self.swipe_minitouch(p1, p2, speed=speed, method=method)
