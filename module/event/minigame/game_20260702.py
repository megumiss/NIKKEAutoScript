from module.base.timer import Timer
from module.conversation.assets import ANSWER_CHECK
from module.event.event_20260702.assets import SKIP
from module.event.event_20260702.assets_game import *
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
    while 1:
        self.device.screenshot()

        # 结束
        if self.appear_then_click(MINI_GAME_BACK, offset=10, interval=2):
            logger.info('Event mini game done')
            continue

        # 关闭弹窗
        if self.appear_then_click(MINI_GAME_EXEC_CLOSE, offset=30, interval=1, static=False):
            continue

        if not self.appear(MINI_GAME_CHECK, offset=10):
            # 点击方块
            box = self.appear_location(MINI_GAME_EXEC_TARGET_1, offset=30, static=False)
            if box:
                self.device.swipe(box, (box[0] - 30, box[1]), method='swipe', speed=30, handle_control_check=False)
                self.device.sleep(2)
                continue
            box = self.appear_location(MINI_GAME_EXEC_TARGET_2, offset=30, static=False)
            if box:
                self.device.swipe(box, (box[0] - 30, box[1]), method='swipe', handle_control_check=False)
                self.device.sleep(2)
                continue
            box = self.appear_location(MINI_GAME_EXEC_TARGET_3, offset=30, static=False)
            if box:
                self.device.swipe(box, (box[0] - 30, box[1]), method='swipe', speed=30, handle_control_check=False)
                self.device.sleep(2)
                continue
            box = self.appear_location(MINI_GAME_EXEC_TARGET_4, offset=30, threshold=0.6, static=False)
            if box:
                self.device.swipe(box, (box[0] - 30, box[1]), method='swipe', speed=30, handle_control_check=False)
                self.device.sleep(2)
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
