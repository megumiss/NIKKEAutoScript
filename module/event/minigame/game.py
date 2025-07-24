from module.base.timer import Timer
from module.logger import logger
from module.ui.page import *

from .game_20250716 import start_game


def reward(self, skip_first_screenshot=True):
    logger.info('Receive daily reward')
    click_timer = Timer(0.3)

    while 1:
        if skip_first_screenshot:
            skip_first_screenshot = False
        else:
            self.device.screenshot()

        if (
            click_timer.reached()
            and self.appear(self.minigame_assets.MINI_GAME_START, offset=10)
            and self.appear(self.minigame_assets.MINI_GAME_REWARD_DONE, offset=10)
        ):
            break

        if (
            click_timer.reached()
            and self.appear(self.minigame_assets.MINI_GAME_START, offset=10)
            and self.appear_then_click(self.minigame_assets.MINI_GAME_REWARD, offset=10, interval=1)
        ):
            click_timer.reset()
            continue

        # 点击领取
        if click_timer.reached() and self.appear_then_click(
            self.event_assets.RECEIVE, offset=10, interval=1, static=False
        ):
            logger.info('Event mini game receive')
            click_timer.reset()
            continue


def mission(self, skip_first_screenshot=True):
    logger.info('Receive mission reward')
    click_timer = Timer(0.3)

    while 1:
        if skip_first_screenshot:
            skip_first_screenshot = False
        else:
            self.device.screenshot()

        if (
            click_timer.reached()
            and self.appear(self.minigame_assets.MINI_GAME_START, offset=10)
            and self.appear(self.minigame_assets.MINI_GAME_REWARD_DONE, offset=10)
        ):
            break

        if (
            click_timer.reached()
            and self.appear(self.minigame_assets.MINI_GAME_START, offset=10)
            and self.appear_then_click(self.minigame_assets.MINI_GAME_REWARD, offset=10, interval=1)
        ):
            click_timer.reset()
            continue

        # 点击领取
        if click_timer.reached() and self.appear_then_click(
            self.event_assets.RECEIVE, offset=10, interval=1, static=False
        ):
            logger.info('Event mini game receive')
            click_timer.reset()
            continue


def back_to_event(self, skip_first_screenshot=True):
    logger.info('Mini game done, back to event')
    click_timer = Timer(0.3)

    while 1:
        if skip_first_screenshot:
            skip_first_screenshot = False
        else:
            self.device.screenshot()

        if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
            break

        if click_timer.reached() and self.appear_then_click(GOTO_BACK, offset=10, interval=2):
            click_timer.reset()
            continue

        if click_timer.reached() and self.appear_then_click(
            self.minigame_assets.MINI_GAME_BACK_CONFIRM, offset=10, interval=2
        ):
            click_timer.reset()
            continue


def game(self):
    logger.info('Open event mini game')

    # 每日
    #start_game(self)
    # 领取每日奖励
    reward(self)
    # 领取任务奖励
    mission(self)
    # 回到活动主页
    back_to_event(self)
