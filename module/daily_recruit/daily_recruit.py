import os
import time
from datetime import datetime

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
    def save_drop_image(self, image, base_path):
        """
        保存抽卡截图到指定文件夹，并以日期+时间为文件名
        兼容 Linux/Windows
        Args:
            image: OpenCV 格式图片 (numpy.ndarray)
            base_path: 基础保存路径
        Returns:
            save_path: 保存的完整文件路径
        """
        if not base_path:
            return None

        # 拼接保存目录：base_path/config_name
        save_dir = os.path.join(base_path, self.config.config_name)
        os.makedirs(save_dir, exist_ok=True)

        # 生成日期+时间文件名，例如 2025-10-30_23-59-41.png
        datetime_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        filename = f'{datetime_str}.png'
        save_path = os.path.join(save_dir, filename)

        # 保存图片
        from module.base.utils import save_image

        save_image(image, save_path)

        return save_path

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
            if self.appear(RECRUIT_CONFIRM, offset=(30, 30), static=False):
                saved_path = self.save_drop_image(self.device.image, self.config.DailyRecruit_ScreenshotPath)
                if saved_path:
                    logger.info(f'Save recruit image to: {saved_path}')

                while 1:
                    self.device.screenshot()
                    if not self.appear(RECRUIT_CONFIRM, offset=(30, 30), static=False):
                        break
                    if self.appear_then_click(RECRUIT_CONFIRM, offset=(30, 30), interval=2, static=False):
                        continue

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
            if self.appear(RECRUIT_CONFIRM, offset=(30, 30), static=False):
                saved_path = self.save_drop_image(self.device.image, self.config.DailyRecruit_ScreenshotPath)
                if saved_path:
                    logger.info(f'Save recruit image to: {saved_path}')

                while 1:
                    self.device.screenshot()
                    if not self.appear(RECRUIT_CONFIRM, offset=(30, 30), static=False):
                        break
                    if self.appear_then_click(RECRUIT_CONFIRM, offset=(30, 30), interval=2, static=False):
                        continue

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
