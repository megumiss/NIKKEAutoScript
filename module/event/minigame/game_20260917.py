import random

from module.base.timer import Timer
from module.conversation.assets import ANSWER_CHECK
from module.event.event_20260917.assets import SKIP
from module.event.event_20260917.assets_game import *
from module.logger import logger
from module.ui.page import *


def start_game(self, skip_first_screenshot=True):
    logger.info('Open event mini game')
    confirm_timer = Timer(2, count=3)

    # 游戏开始
    while 1:
        if skip_first_screenshot:
            skip_first_screenshot = False
        else:
            self.device.screenshot()

        # 点击开始
        if self.appear_then_click(MINI_GAME_START, offset=10, interval=2):
            logger.info('Start event mini game')
            continue

        # 点击开始
        if self.appear_then_click(MINI_GAME_START_CONFIRM, offset=10, interval=2):
            logger.info('Start event mini game confirm')
            continue

        # 关闭弹窗
        # if self.appear_then_click(MINI_GAME_EXEC_CLOSE, offset=30, interval=1, static=False):
        #     continue

        if self.appear(MINI_GAME_EXEC_CHECK, offset=10):
            break

    # 游戏逻辑处理
    jump_timer = Timer(3)
    jump_to_left = True
    while 1:
        self.device.screenshot()

        # 回到小游戏主页
        if self.appear(MINI_GAME_CHECK, offset=10):
            break

        # 结束
        if self.appear_then_click(MINI_GAME_BACK, offset=10, interval=2):
            logger.info('Event mini game done')
            continue

        # 关闭弹窗
        if self.appear_then_click(MINI_GAME_EXEC_CLOSE, offset=30, interval=1, static=False):
            continue

        # 左右横跳：每 5 秒点一次半屏，依次 左 -> 右 -> 左
        if jump_timer.reached_and_reset():
            x = random.randint(150, 250) if jump_to_left else random.randint(470, 570)
            y = random.randint(560, 720)
            logger.info(f'Event mini game jump to {"left" if jump_to_left else "right"} @ ({x}, {y})')
            self.device.click_xy(x, y)
            jump_to_left = not jump_to_left
            continue

        # 跳过对话
        if self.config.Event_GameStorySkip and self.appear_then_click(SKIP, offset=10, interval=1):
            continue
        # 选择对话选项
        if self.appear_then_click(ANSWER_CHECK, offset=10, interval=1, static=False):
            continue

        # 关闭窗口
        if self.appear_then_click(MINI_GAME_CLOSE, offset=10, interval=1, static=False):
            continue

        # 回到小游戏主页
        if self.appear(MINI_GAME_CHECK, offset=10):
            if not confirm_timer.started():
                confirm_timer.start()

            if confirm_timer.reached():
                break
        else:
            confirm_timer.clear()
