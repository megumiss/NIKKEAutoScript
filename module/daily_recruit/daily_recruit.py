import time

from module.base.timer import Timer
from module.base.utils import point2str
from module.daily_recruit.assets import *
from module.logger import logger
from module.reward.assets import *
from module.ui.page import *
from module.ui.ui import UI


class EndEventFree(Exception):
    pass


class NotEnoughSocialPoint(Exception):
    pass


class DailyRecruit(UI):
    def event_free_recruit(self, skip_first_screenshot=True):
        logger.hr('Event free recruit')
        confirm_timer = Timer(5, count=3).start()
        click_timer = Timer(0.5)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 跳到普通招募页面时结束抽卡
            if self.appear(ORDINARY_RECRUIT_CHECK, offset=(5, 5), interval=1, static=False):
                logger.info('Event free recruit has done')
                raise EndEventFree

            # 免费抽卡
            if not self.appear(FREE_RECRUIT_CHECK, offset=(5, 5), interval=1, static=False):
                # 向右点击
                logger.info('Click %s @ %s' % (point2str(690, 670), 'TO_RIGHT_RECRUIT'))
                self.device.click_minitouch(690, 670)
                click_timer.reset()
                time.sleep(1)
                continue
            else:
                break

        recruit_end = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 抽卡
            if (
                not recruit_end
                and click_timer.reached()
                and self.appear(FREE_RECRUIT_CHECK, offset=(10, 10), interval=1, static=False)
            ):
                logger.info('Click %s @ %s' % (point2str(130, 1050), 'FREE_RECRUIT'))
                self.device.click_minitouch(130, 1050)
                click_timer.reset()
                time.sleep(1)
                continue
            # 跳过
            if click_timer.reached() and self.appear_then_click(
                RECRUIT_SKIP, offset=(30, 30), interval=1, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue
            # 确认
            if click_timer.reached() and self.appear_then_click(
                RECRUIT_CONFIRM, offset=(30, 30), interval=3, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                recruit_end = True
                continue
            # 结束
            if (
                recruit_end
                and self.appear(SPECIAL_RECRUIT_CHECK, offset=(10, 10), static=False)
                and confirm_timer.reached()
            ):
                self.event_free_recruit()

        logger.info('Event free recruit has done')
        return True

    def social_point_recruit(self, skip_first_screenshot=True):
        logger.hr('Social point recruit')
        confirm_timer = Timer(5, count=3).start()
        click_timer = Timer(0.5)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 友情点抽卡
            if not self.appear(SOCIAL_RECRUIT_CHECK, offset=(5, 5), interval=1, static=False):
                # 向左点击
                logger.info('Click %s @ %s' % (point2str(30, 670), 'TO_LEFT_RECRUIT'))
                self.device.click_minitouch(30, 670)
                click_timer.reset()
                time.sleep(1)
                continue
            else:
                break

        recruit_end = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 友情点不足
            if self.appear(SOCIAL_POINT_NOT_ENOUGH, static=False):
                logger.info('There are not enough social point')
                raise NotEnoughSocialPoint

            # 抽卡
            if (
                not recruit_end
                and click_timer.reached()
                and self.appear_then_click(SOCIAL_RECRUIT, offset=(30, 30), interval=3)
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue
            # 跳过
            if click_timer.reached() and self.appear_then_click(
                RECRUIT_SKIP, offset=(30, 30), interval=1, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue
            # 确认
            if click_timer.reached() and self.appear_then_click(
                RECRUIT_CONFIRM, offset=(30, 30), interval=3, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                recruit_end = True
                continue
            # 结束
            if (
                recruit_end
                and self.appear(SOCIAL_RECRUIT_CHECK, offset=(10, 10), static=False)
                and confirm_timer.reached()
            ):
                break

        logger.info('Social point recruit has done')
        return True

    def run(self):
        self.ui_ensure(page_recruit)
        # 活动免费单抽
        if self.config.DailyRecruit_EventFreeRecruit:
            try:
                self.event_free_recruit()
            except EndEventFree:
                pass
        # 友情点单抽
        if self.config.DailyRecruit_SocialPointRecruit:
            try:
                self.social_point_recruit()
            except NotEnoughSocialPoint:
                pass
        self.config.task_delay(server_update=True)
