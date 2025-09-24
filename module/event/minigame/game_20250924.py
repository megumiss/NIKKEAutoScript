from module.base.timer import Timer
from module.conversation.assets import ANSWER_CHECK
from module.event.event_20250924.assets import SKIP
from module.event.event_20250924.assets_game import *
from module.logger import logger
from module.ui.page import *


def start_game(self, skip_first_screenshot=True):
    logger.info('Open event mini game')
    confirm_timer = Timer(2, count=3)
    click_timer = Timer(0.3)

    # 游戏开始
    while 1:
        if skip_first_screenshot:
            skip_first_screenshot = False
        else:
            self.device.screenshot()

        # 点击开始
        if click_timer.reached() and self.appear_then_click(MINI_GAME_START, offset=10, interval=2):
            logger.info('Start event mini game')
            click_timer.reset()
            continue

        # 点击开始
        if click_timer.reached() and self.appear_then_click(MINI_GAME_START_CONFIRM, offset=10, interval=2):
            logger.info('Start event mini game confirm')
            click_timer.reset()
            continue

        # 商店点击开始
        if click_timer.reached() and self.appear_then_click(MINI_GAME_SHOP_START, offset=10, interval=2):
            click_timer.reset()
            continue

        # 关闭弹窗
        if click_timer.reached() and self.appear_then_click(MINI_GAME_EXEC_CLOSE, offset=30, interval=1, static=False):
            click_timer.reset()
            continue

        if self.appear(MINI_GAME_EXEC_CHECK, offset=10):
            break

    # 游戏逻辑处理
    while 1:
        self.device.screenshot()

        # 结束
        if click_timer.reached() and self.appear_then_click(MINI_GAME_BACK, offset=10, interval=2):
            logger.info('Event mini game done')
            click_timer.reset()
            continue

        # 关闭弹窗
        if click_timer.reached() and self.appear_then_click(MINI_GAME_EXEC_CLOSE, offset=30, interval=1, static=False):
            click_timer.reset()
            continue

        # 加速1
        if click_timer.reached() and self.appear_then_click(MINI_GAME_EXEC_1X, offset=30, interval=1):
            click_timer.reset()
            continue
        # 加速2
        if click_timer.reached() and self.appear_then_click(MINI_GAME_EXEC_2X, offset=30, interval=1):
            click_timer.reset()
            continue

        # 商店战斗开始
        if click_timer.reached() and self.appear_then_click(MINI_GAME_SHOP_START, offset=10, interval=1):
            click_timer.reset()
            continue

        # buff
        if click_timer.reached() and self.appear_then_click(MINI_GAME_EXEC_BUFF_1, offset=30, interval=1):
            click_timer.reset()
            continue
        if click_timer.reached() and self.appear_then_click(MINI_GAME_EXEC_BUFF_SELECT, offset=30, interval=1):
            click_timer.reset()
            continue

        # 背包
        if click_timer.reached() and self.appear_then_click(MINI_GAME_EXEC_BAG_DONE, threshold=10, interval=1):
            click_timer.reset()
            continue
        if click_timer.reached() and self.appear_then_click(MINI_GAME_EXEC_BAG, offset=30, interval=0.5, static=False):
            click_timer.reset()
            continue

        # 跳过对话
        if (
            self.config.Event_GameStorySkip
            and click_timer.reached()
            and self.appear_then_click(SKIP, offset=10, interval=1)
        ):
            click_timer.reset()
            continue
        # 选择对话选项
        if click_timer.reached() and self.appear_then_click(ANSWER_CHECK, offset=10, interval=1, static=False):
            click_timer.reset()
            continue

        # 关闭窗口
        if click_timer.reached() and self.appear_then_click(MINI_GAME_CLOSE, offset=10, interval=1, static=False):
            click_timer.reset()
            continue

        # 回到小游戏主页
        if self.appear(MINI_GAME_CHECK, offset=10):
            if not confirm_timer.started():
                confirm_timer.start()

            if confirm_timer.reached():
                break
        else:
            confirm_timer.clear()
