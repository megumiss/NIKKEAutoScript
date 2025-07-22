from module.base.timer import Timer
from module.event.event_20250716.assets import *
from module.logger import logger
from module.ui.page import *


def start_game(self):
    logger.info('Open event mini game')
    click_timer = Timer(0.3)

    while 1:
        self.device.screenshot()

        # 结束
        if click_timer.reached() and self.appear_then_click(self.event_assets.MINI_GAME_BACK, offset=10, interval=2):
            logger.info('Event mini game done')
            click_timer.reset()
            continue

        # 技能1
        if (
            click_timer.reached()
            and self.appear(self.event_assets.MINI_GAME_TIME_OUT, offset=10)
            and self.appear_then_click(self.event_assets.MINI_GAME_SKILL1, offset=10, interval=10)
        ):
            click_timer.reset()
            continue

        # 技能2
        if (
            click_timer.reached()
            and self.appear(self.event_assets.MINI_GAME_TIME_OUT, offset=10)
            and self.appear_then_click(self.event_assets.MINI_GAME_SKILL2, offset=10, interval=10)
        ):
            click_timer.reset()
            continue

        # 循环点击
        if self.appear(self.event_assets.MINI_GAME_CLICK, offset=10):
            self.device.click_minitouch(360, 1000)
            self.device.sleep(0.5)
            continue

        if self.appear(self.event_assets.MINI_GAME_START, offset=10):
            break

        # 关闭窗口
        if click_timer.reached() and self.appear_then_click(
            self.event_assets.MINI_GAME_CLOSE, offset=10, interval=1, static=False
        ):
            click_timer.reset()
            continue
    