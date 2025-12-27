from module.base.decorator import Config
from module.base.timer import Timer
from module.event.assets import RECEIVE
from module.event.base import EventBase
from module.logger import logger
from module.ui.assets import GOTO_BACK, NEW_NIKKE_CONFIRM


class EventLogin(EventBase):
    @Config.when(EVENT_TYPE=1)
    def login_stamp(self, skip_first_screenshot=True):
        logger.hr('START EVENT LOGIN STAMP', 2)
        click_timer = Timer(0.3)

        # 进入签到页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.LOGIN_STAMP, offset=10, interval=5)
            ):
                click_timer.reset()
                continue

            if self.appear(self.event_assets.LOGIN_STAMP_CHECK, offset=10):
                click_timer.reset()
                break

        # 签到
        event_timer = Timer(3, count=5)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 返回活动页面
            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                if not event_timer.started():
                    event_timer.start()
                if event_timer.reached():
                    break
            else:
                event_timer.clear()

            # 返回
            if (
                click_timer.reached()
                and self.appear(self.event_assets.LOGIN_STAMP_CHECK, offset=10)
                and self.appear(self.event_assets.LOGIN_STAMP_DONE, threshold=10)
                and self.appear_then_click(GOTO_BACK, offset=(30, 30), interval=2)
            ):
                click_timer.reset()
                continue

            # 全部领取
            if (
                click_timer.reached()
                and self.appear(self.event_assets.LOGIN_STAMP_CHECK, offset=10)
                and self.appear_then_click(self.event_assets.LOGIN_STAMP_REWARD, threshold=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 点击领取
            if click_timer.reached() and self.appear_then_click(RECEIVE, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

            # 点击跳过
            if click_timer.reached() and self.appear_then_click(
                self.event_assets.SKIP, offset=(30, 10), threshold=0.65, interval=1
            ):
                click_timer.reset()
                continue

            # 点击确认
            if click_timer.reached() and self.appear_then_click(NEW_NIKKE_CONFIRM, offset=30, interval=1):
                click_timer.reset()
                continue

        # self.ui_ensure(page_event)
        logger.info('Login stamp done')

    @Config.when(EVENT_TYPE=(2, 3))
    def login_stamp(self):
        logger.hr('START EVENT LOGIN STAMP', 2)
        logger.info('Small event, skip loginstamp')
