from module.base.timer import Timer
from module.base.utils import point2str
from module.config.delay import next_month, next_tuesday
from module.gift.assets import *
from module.handler.assets import ANNOUNCEMENT
from module.logger import logger
from module.ui.assets import CASH_SHOP_CHECK, GOTO_BACK, MAIN_CHECK, MAIN_GOTO_CASH_SHOP
from module.ui.page import page_main
from module.ui.ui import UI


class NetworkError(Exception):
    pass


class GiftNotFound(Exception):
    pass


class GiftBase(UI):
    def _run(self, button, check):
        if not self.appear(CASH_SHOP_CHECK, offset=(10, 10)):
            self.ui_ensure(page_main)
            self.ensure_into_shop()
        try:
            if button == STEPUP:
                self.ensure_into_shop_limited_time()
            else:
                self.ensure_into_shop_general()
            self.receive_available_gift(button, check)
        except NetworkError:
            logger.error('Cannot access the cash shop under the current network')
            logger.error("If you haven't logged into Google Play, please log in and try again.")
            self.ensure_back()
        except GiftNotFound:
            pass

    def ensure_into_shop(self, skip_first_screenshot=True):
        logger.info('Open cash shop')
        click_timer = Timer(0.3)
        confirm_timer = Timer(3, count=3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 付费
            if self.appear(CASH_SHOP_CHECK, offset=10):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    logger.info('Open cash shop done')
                    break
            else:
                confirm_timer.clear()

            # 打开商店
            if click_timer.reached() and self.appear_then_click(MAIN_GOTO_CASH_SHOP, offset=30, interval=3):
                click_timer.reset()
                continue

            # 消费限制
            if (
                click_timer.reached()
                and self.appear(CONSUMPTION_RESTRICTIONS, offset=10)
                and self.appear_then_click(AGE_20, threshold=10, interval=1)
            ):
                click_timer.reset()
                continue
            if (
                click_timer.reached()
                and self.appear(CONSUMPTION_RESTRICTIONS, offset=10)
                and self.appear_then_click(CONFIRM, threshold=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 操控妮姬的感觉
            if click_timer.reached() and self.handle_popup():
                click_timer.reset()
                continue

            if self.appear(FAILED_CHECK, offset=(30, 30)):
                raise NetworkError

    def ensure_into_shop_general(self, skip_first_screenshot=True):
        logger.info('Open cash shop general')
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 每月礼包
            if self.appear(GENERAL_GIFT_CHECK, offset=(10, 10)) and self.appear(
                MONTHLY, threshold=0.7, offset=(100, 10)
            ):
                logger.info('Open cash shop general done')
                break

            # 打开礼礼包页面
            if click_timer.reached() and self.appear_then_click(
                GOTO_GENERAL_GIFT, offset=(120, 10), threshold=0.95, interval=1
            ):
                click_timer.reset()
                continue

            # 检查每月礼包
            if click_timer.reached() and self.appear(GENERAL_GIFT_CHECK, offset=(10, 10)):
                self.ensure_sroll((590, 360), (300, 360), method='swipe', count=1, delay=1)
                click_timer.reset()
                continue

            if self.appear(FAILED_CHECK, offset=(30, 30)):
                raise NetworkError

    def ensure_into_shop_limited_time(self, skip_first_screenshot=True):
        logger.info('Open cash shop limited')
        confirm_timer = Timer(2, count=3)
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # STEPUP礼包检查
            if self.appear(LIMITED_GIFT_CHECK, offset=(100, 30)):
                if self.appear(STEPUP, offset=(600, 10), threshold=0.7):
                    logger.info('Open cash shop limited done')
                    break
                # 没有找到STEPUP按钮
                if not self.appear(STEPUP, offset=(600, 10), threshold=0.7):
                    if not confirm_timer.started():
                        confirm_timer.start()
                    if confirm_timer.reached():
                        logger.warning('StepUp gift allready closed')
                        raise GiftNotFound
                else:
                    confirm_timer.clear()

            # 打开礼包页面
            if click_timer.reached() and self.appear_then_click(
                GOTO_STEPUP_GIFT, offset=(120, 10), threshold=0.95, interval=1
            ):
                click_timer.reset()
                continue

            if self.appear(FAILED_CHECK, offset=(30, 30)):
                raise NetworkError

    def receive_available_gift(self, button, check, skip_first_screenshot=True):
        logger.info(f'Receive gift {button.name}')
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

            if click_timer.reached() and self.appear_then_click(button, offset=(600, 10), threshold=0.8, interval=2):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(check, offset=(100, 10)) and confirm_timer.reached():
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

            if click_timer.reached() and self.appear_then_click(GIFT_B, offset=10, threshold=0.99, interval=1):
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
                logger.info(f'Receive gift {button.name} done')
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
    def run(self):
        self._run(WEEKLY, WEEKLY_CHECK)
        self.config.task_delay(target=next_tuesday())


class MonthlyGift(GiftBase):
    def run(self):
        self._run(MONTHLY, MONTHLY_CHECK)
        self.config.task_delay(target=next_month())


class StepUpGift(GiftBase):
    def run(self):
        self._run(STEPUP, STEPUP_CHECK)
        self.config.task_delay(target=next_tuesday())
