import random

from module.base.timer import Timer
from module.conversation.assets import ANSWER_CHECK
from module.event.event_20260813.assets import SKIP
from module.event.event_20260813.assets_game import *
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
        if self.appear_then_click(MINI_GAME_EXEC_CLOSE, offset=30, interval=1, static=False):
            continue

        if self.appear(MINI_GAME_EXEC_CHECK, offset=10):
            break

    # 游戏逻辑处理
    moved = False
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

        # 如果任务在初始位置，走两步
        if moved or self.appear(MINI_GAME_EXEC_DEFAULT_LOC, offset=10, interval=1):
            if not moved:
                self.device.sleep(1)

            # 仅 PC 客户端支持按键操作
            if self.config.CLIENT_PLATFORM == 'win' and self.config.PCClientInfo_ControlScheme == 'pyautogui':
                self.device.secretly_press_key('w', wait_time=0.2)
                self.device.secretly_press_key(random.choice(['a', 'd']), wait_time=0.2)
            else:
                # w
                self.ensure_sroll((350, 750), (400, 650), method='swipe', count=1, delay=0.2)
                # a / d 随机一个
                p1, p2 = random.choice([((350, 650), (200, 600)), ((350, 650), (600, 750))])
                self.ensure_sroll(p1, p2, method='swipe', count=1, delay=0.2)
            moved = True
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
