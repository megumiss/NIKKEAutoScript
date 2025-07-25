from module.base.timer import Timer
from module.event.event_20250716.assets_game import *
from module.logger import logger
from module.ui.page import *


def start_game(self, skip_first_screenshot=True):
    logger.info('Open event mini game')
    confirm_timer = Timer(3, count=3)
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

        # 技能1
        if (
            click_timer.reached()
            and self.appear(MINI_GAME_TIME_OUT, offset=10)
            and self.appear_then_click(MINI_GAME_SKILL1, offset=10, interval=10)
        ):
            click_timer.reset()
            continue

        # 技能2
        if (
            click_timer.reached()
            and self.appear(MINI_GAME_TIME_OUT, offset=10)
            and self.appear_then_click(MINI_GAME_SKILL2, offset=10, interval=10)
        ):
            click_timer.reset()
            continue

        # 循环点击
        if self.appear(MINI_GAME_EXEC_CHECK, offset=10):
            self.device.click_minitouch(360, 1000)
            self.device.sleep(0.5)
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
