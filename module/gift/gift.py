from datetime import datetime, timedelta, timezone
from functools import cached_property

from module.base.timer import Timer
from module.base.utils import point2str
from module.gift.assets import *
from module.handler.assets import ANNOUNCEMENT
from module.logger import logger
from module.ui.assets import CASH_SHOP_CHECK, GOTO_BACK, MAIN_CHECK, MAIN_GOTO_CASH_SHOP
from module.ui.page import page_main
from module.ui.ui import UI


class NetworkError(Exception):
    pass


class GiftBase(UI):
    diff = datetime.now(timezone.utc).astimezone().utcoffset() - timedelta(hours=8)

    def _run(self, button, check):
        if not self.appear(CASH_SHOP_CHECK, offset=(10, 10)):
            self.ui_ensure(page_main)
        try:
            self.ensure_into_shop()
            self.receive_available_gift(button, check)
        except NetworkError:
            logger.error('Cannot access the cash shop under the current network')
            logger.error("If you haven't logged into Google Play, please log in and try again.")
            self.ensure_back()

    def ensure_into_shop(self, skip_first_screenshot=True):
        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(MAIN_GOTO_CASH_SHOP, offset=(30, 30), interval=2):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                GOTO_GENERAL_GIFT, offset=(30, 30), interval=6, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.handle_popup():
                confirm_timer.reset()
                click_timer.reset()
                continue

            if (
                click_timer.reached()
                and self.appear(GENERAL_GIFT_CHECK, offset=(10, 10), static=False)
                and not self.appear(MONTHLY, offset=(10, 10), static=False)
            ):
                self.ensure_sroll((590, 360), (300, 360), count=1, delay=0.4)
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(GENERAL_GIFT_CHECK, offset=(10, 10), static=False) and confirm_timer.reached():
                break

            if click_timer.reached() and self.appear(FAILED_CHECK, offset=(30, 30), static=False):
                raise NetworkError

    def receive_available_gift(self, button, check, skip_first_screenshot=True):
        confirm_timer = Timer(3, count=2).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.handle_popup():
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(button, offset=(30, 30), interval=2):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(check, offset=5, static=False) and confirm_timer.reached():
                break

        skip_first_screenshot = True
        confirm_timer.reset()
        click_timer.reset()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(GIFT, offset=5, static=False, interval=2)
                and GIFT.match_appear_on(self.device.image, threshold=25)
            ):
                self.device.click_minitouch(*GIFT.location)
                logger.info('Click %s @ %s' % (point2str(*GIFT.location), 'GIFT'))
                click_timer.reset()
                confirm_timer.reset()
                continue

            if click_timer.reached() and self.handle_popup():
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.handle_reward(1):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(GOTO_BACK, offset=5, static=False) and confirm_timer.reached():
                break

    def ensure_back(self, skip_first_screenshot=True):
        confirm_timer = Timer(2, count=2).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(
                ANNOUNCEMENT, offset=(30, 30), interval=3, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(MAIN_CHECK, offset=(30, 30), static=False) and confirm_timer.reached():
                break


class DailyGift(GiftBase):
    def run(self):
        self._run(DAILY, DAILY_CHECK)
        self.config.task_delay(server_update=True)


class WeeklyGift(GiftBase):
    @cached_property
    def next_tuesday(self) -> datetime:
        local_now = datetime.now()
        remain = (1 - local_now.weekday()) % 7
        remain = remain + 7 if remain == 0 else remain
        return local_now.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=remain) + self.diff

    def run(self):
        self._run(WEEKLY, WEEKLY_CHECK)
        self.config.task_delay(target=self.next_tuesday)


class MonthlyGift(GiftBase):
    @cached_property
    def next_month(self) -> datetime:
        local_now = datetime.now()
        next_month = local_now.month % 12 + 1
        next_year = local_now.year + 1 if next_month == 1 else local_now.year
        return (
            local_now.replace(
                year=next_year,
                month=next_month,
                day=1,
                hour=4,
                minute=0,
                second=0,
                microsecond=0,
            )
            + self.diff
        )

    def run(self):
        self._run(MONTHLY, MONTHLY_CHECK)
        self.config.task_delay(target=self.next_month)
