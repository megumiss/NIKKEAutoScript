import time
from collections import deque
from datetime import datetime
from functools import cached_property

import numpy as np

from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import ensure_int, image_size, point2str
from module.config.config import NikkeConfig
from module.logger import logger

from .input import Input
from .screenshot import Screenshot


class ScreenshotSizeError(Exception):
    pass


class Automation:
    config: NikkeConfig

    """
    自动化管理类，用于管理与游戏窗口相关的自动化操作。
    """

    def __init__(self, config):
        """
        :param window_title: 游戏窗口的标题。
        :param logger: 用于记录日志的Logger对象，可选参数。
        """
        if isinstance(config, str):
            self.config = NikkeConfig(config, task=None)
        else:
            self.config = config
        super().__init__()

        self.window_title = self.config.WinClient_TitleName
        self.window_offset = (0, 0)
        # self.screenshot = None
        self._init_input()
        self.img_cache = {}
        self._screenshot_interval = Timer(float(self.config.Emulator_ScreenshotInterval))

    def _init_input(self):
        """
        初始化输入处理器，将输入操作如点击、移动等绑定至实例变量。
        """
        self.input_handler = Input()
        self.mouse_click = self.input_handler.mouse_click
        self.mouse_down = self.input_handler.mouse_down
        self.mouse_up = self.input_handler.mouse_up
        self.mouse_move = self.input_handler.mouse_move
        self.mouse_scroll = self.input_handler.mouse_scroll
        self.press_key = self.input_handler.press_key
        self.secretly_press_key = self.input_handler.secretly_press_key
        self.press_mouse = self.input_handler.press_mouse

    def screenshot(self, crop=(0, 0, 1, 1)):
        """
        捕获游戏窗口的截图。
        :param crop: 截图的裁剪区域，格式为(x1, y1, x2, y2)，默认为全屏。
        :return: 成功时返回截图及其位置和缩放因子，失败时抛出异常。
        """
        # 两次截图间隔时间
        self._screenshot_interval.wait()
        self._screenshot_interval.reset()

        start_time = time.time()
        while True:
            try:
                result = Screenshot.take_screenshot(self.window_title, self.config.WinClient_Screens, crop=crop)
                if result:
                    self.image, self.screenshot_pos, self.screenshot_scale_factor = result
                    self.window_offset = self.screenshot_pos[0], self.screenshot_pos[1]
                    self.image = self._handle_orientated_image(self.image)
                    self.screenshot_deque.append({'time': datetime.now(), 'image': self.image})
                    # cv2.imwrite('debug_screenshot2.png', np.array(self.image))
                    return result
                else:
                    logger.error('截图失败：没有找到游戏窗口')
            except Exception as e:
                logger.error(f'截图失败：{e}')
            time.sleep(1)
            if time.time() - start_time > 30:
                raise RuntimeError('截图超时')

    @cached_property
    def screenshot_deque(self):
        return deque(maxlen=int(self.config.Error_ScreenshotLength))

    def _handle_orientated_image(self, image):
        """
        Args:
            image (np.ndarray):

        Returns:
            np.ndarray:
        """
        width, height = image_size(self.image)
        if width == 720 or height == 1280:
            return image

        raise ScreenshotSizeError("The emulator's display size must be 720*1280")

    def click(self, button: Button, click_offset=0, action='click'):
        """Method to click a button.

        Args:
            button (button.Button): AzurLane Button instance.
            control_check (bool):
        """
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
        logger.info('Click %s @ %s' % (point2str(x, y), button))

        x += self.window_offset[0]
        y += self.window_offset[1]
        # x, y = self.calculate_click_position(coordinates, offset)
        # 动作到方法的映射
        action_map = {
            'click': self.mouse_click,
            'down': self.mouse_down,
            'move': self.mouse_move,
        }

        if action in action_map:
            action_map[action](x, y)
        else:
            raise ValueError(f'未知的动作类型: {action}')

    def click_minitouch(self, x, y, action='click'):
        x += self.window_offset[0]
        y += self.window_offset[1]
        # 动作到方法的映射
        action_map = {
            'click': self.mouse_click,
            'down': self.mouse_down,
            'move': self.mouse_move,
        }

        if action in action_map:
            action_map[action](x, y)
        else:
            raise ValueError(f'未知的动作类型: {action}')

    def swipe(
        self, p1, p2, speed=15, hold=0, name='SWIPE', label='Swipe', distance_check=True, handle_control_check=True
    ):
        if handle_control_check:
            self.handle_control_check(name)
        p1, p2 = ensure_int(p1, p2)
        method = self.config.Emulator_ControlMethod
        if method == 'minitouch':
            logger.info('%s %s -> %s' % (label, point2str(*p1), point2str(*p2)))

        if distance_check:
            if np.linalg.norm(np.subtract(p1, p2)) < 10:
                # Should swipe a certain distance, otherwise AL will treat it as click.
                # uiautomator2 should >= 6px, minitouch should >= 5px
                logger.info('Swipe distance < 10px, dropped')
                return

        if method == 'minitouch':
            self.swipe_minitouch(p1, p2, speed=speed, hold=hold)

    def calculate_click_position(self, coordinates, offset=(0, 0)):
        """
        计算实际点击位置的坐标。

        参数:
        - coordinates: 元组，表示元素的坐标，格式为((left, top), (right, bottom))。
        - offset: 元组，表示相对于元素中心的偏移量，格式为(x_offset, y_offset)。

        返回:
        - (x, y): 元组，表示计算后的点击位置坐标。
        """
        (left, top), (right, bottom) = coordinates
        x = (left + right) // 2 + offset[0]
        y = (top + bottom) // 2 + offset[1]
        return x, y
