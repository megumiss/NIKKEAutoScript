from module.base.decorator import Config
from module.base.timer import Timer
from module.event.base import EventBase
from module.event.minigame.game import game
from module.logger import logger


class EventGame(EventBase):
    @Config.when(EVENT_MINI_GAME=True)
    def game(self, skip_first_screenshot=True):
        logger.hr('START EVENT GAME', 2)
        click_timer = Timer(0.3)

        # 进入小游戏页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.minigame_assets.MINI_GAME, offset=10, interval=5)
            ):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                self.minigame_assets.MINI_GAME_TOUCH, offset=10, interval=2
            ):
                click_timer.reset()
                continue

            # 跳过对话
            if (
                self.config.Event_GameStorySkip
                and click_timer.reached()
                and self.appear_then_click(self.event_assets.SKIP, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            if self.appear(self.minigame_assets.MINI_GAME_CHECK, offset=10):
                break

        return game(self)

    @Config.when(EVENT_MINI_GAME=False)
    def game(self):
        logger.hr('START EVENT GAME', 2)
        logger.info('Game not support in this event')
