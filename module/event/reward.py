from module.base.decorator import Config
from module.base.timer import Timer
from module.event.assets import RECEIVE, RECEIVE_REWARD
from module.event.base import EventBase
from module.logger import logger


class EventReward(EventBase):
    @Config.when(EVENT_TYPE=1)
    def reward(self, skip_first_screenshot=True):
        logger.hr('START EVENT REWARD', 2)
        click_timer = Timer(0.3)

        # 进入任务页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.REWARD, offset=10, interval=3)
            ):
                click_timer.reset()
                continue

            if self.appear(self.event_assets.REWARD_CHECK, offset=10):
                self.device.sleep(1)
                break

        # 领取奖励
        while 1:
            self.device.screenshot()

            # 返回活动页面
            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            # 关闭
            if (
                click_timer.reached()
                and self.appear(self.event_assets.REWARD_CHALLENGE_CHECK, threshold=5)
                and self.appear(self.event_assets.REWARD_RECEIVE_DONE, threshold=5)
                and self.appear_then_click(self.event_assets.REWARD_CLOSED, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 领取
            if click_timer.reached() and self.appear_then_click(
                self.event_assets.REWARD_RECEIVE, threshold=10, interval=1
            ):
                click_timer.reset()
                continue

            # 点击领取
            if click_timer.reached() and self.appear_then_click(RECEIVE, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

            # 点击reward领取
            if click_timer.reached() and self.appear_then_click(RECEIVE_REWARD, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

            # 进入成就页面
            if (
                click_timer.reached()
                and self.appear(self.event_assets.REWARD_MISSION_CHECK, threshold=5)
                and self.appear(self.event_assets.REWARD_MISSION_CLEARED, offset=10)
                and self.appear_then_click(
                    self.event_assets.REWARD_CHALLENGE_HIDDEN, offset=10, threshold=0.95, interval=1
                )
            ):
                click_timer.reset()
                continue

            # 进入成就页面
            if (
                click_timer.reached()
                and self.appear(self.event_assets.REWARD_MISSION_CHECK, threshold=5)
                and self.appear(self.event_assets.REWARD_RECEIVE_DONE, threshold=5)
                and self.appear_then_click(
                    self.event_assets.REWARD_CHALLENGE_HIDDEN, offset=10, threshold=0.95, interval=1
                )
            ):
                click_timer.reset()
                continue

        logger.info('Event reward done')

    @Config.when(EVENT_TYPE=(2, 3))
    def reward(self, skip_first_screenshot=True):
        logger.hr('START EVENT REWARD', 2)
        click_timer = Timer(0.3)

        # 进入任务页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.REWARD, offset=10, interval=3)
            ):
                click_timer.reset()
                continue

            if self.appear(self.event_assets.REWARD_CHECK, offset=10):
                break

        # 领取奖励
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 返回活动页面
            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            # 关闭
            if (
                click_timer.reached()
                and self.appear(self.event_assets.REWARD_RECEIVE_DONE, threshold=10)
                and self.appear_then_click(self.event_assets.REWARD_CLOSED, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 领取
            if click_timer.reached() and self.appear_then_click(
                self.event_assets.REWARD_RECEIVE, threshold=10, interval=1
            ):
                click_timer.reset()
                continue

            # 点击领取
            if click_timer.reached() and self.appear_then_click(RECEIVE, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

            # 点击reward领取
            if click_timer.reached() and self.appear_then_click(RECEIVE_REWARD, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

        logger.info('Event reward done')
