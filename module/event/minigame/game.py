import importlib

from module.base.timer import Timer
from module.logger import logger
from module.ui.page import *


def reward(self, skip_first_screenshot=True):
    logger.info('Receive daily reward')
    click_timer = Timer(0.3)

    while 1:
        if skip_first_screenshot:
            skip_first_screenshot = False
        else:
            self.device.screenshot()

        if self.appear(self.minigame_assets.MINI_GAME_CHECK, offset=10) and self.appear(
            self.minigame_assets.MINI_GAME_REWARD_DONE, offset=10
        ):
            break

        if (
            click_timer.reached()
            and self.appear(self.minigame_assets.MINI_GAME_CHECK, offset=10)
            and self.appear_then_click(self.minigame_assets.MINI_GAME_REWARD, offset=10, interval=1)
        ):
            self.device.sleep(0.5)
            click_timer.reset()
            continue

        # 点击领取
        if click_timer.reached() and self.appear_then_click(
            self.event_assets.RECEIVE, offset=10, interval=1, static=False
        ):
            click_timer.reset()
            continue

    logger.info('Receive daily reward done')


def mission(self, skip_first_screenshot=True):
    logger.info('Receive mission reward')
    click_timer = Timer(0.3)

    # 打开任务弹窗
    while 1:
        if skip_first_screenshot:
            skip_first_screenshot = False
        else:
            self.device.screenshot()

        if (
            click_timer.reached()
            and self.appear(self.minigame_assets.MINI_GAME_CHECK, offset=10)
            and self.appear_then_click(self.minigame_assets.MINI_GAME_MISSION, offset=10, interval=1)
        ):
            click_timer.reset()
            continue

        if self.appear(self.minigame_assets.MINI_GAME_MISSION_CHECK, offset=10):
            break
    logger.info('Receive mission opened')

    # 领取奖励
    while 1:
        self.device.screenshot()

        # 返回小游戏页面
        if self.appear(self.minigame_assets.MINI_GAME_CHECK, offset=10):
            break

        # 关闭
        if (
            click_timer.reached()
            and self.appear(self.minigame_assets.MINI_GAME_MISSION_DONE, threshold=10)
            and self.appear_then_click(self.minigame_assets.MINI_GAME_MISSION_CLOSE, offset=10, interval=1)
        ):
            click_timer.reset()
            continue

        # 领取
        if click_timer.reached() and self.appear_then_click(
            self.minigame_assets.MINI_GAME_MISSION_REWARD, threshold=10, interval=1
        ):
            click_timer.reset()
            continue

        # 点击领取
        if click_timer.reached() and self.appear_then_click(
            self.event_assets.RECEIVE, offset=10, interval=1, static=False
        ):
            click_timer.reset()
            continue

    logger.info('Receive mission reward done')


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

    logger.info('Mini game done, back to event done')


def start_game(self):
    event_id = self.event.id
    module_name = f'.game_{event_id.split("event_", 1)[1]}'
    game_module = importlib.import_module(module_name, package=__package__)

    return game_module.start_game(self)


def game(self):
    logger.info('Open event mini game')

    # 每日
    start_game(self)
    # 领取每日奖励
    reward(self)
    # 领取任务奖励
    mission(self)
    # 回到活动主页
    back_to_event(self)
