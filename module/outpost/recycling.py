from datetime import datetime, timedelta, timezone
from functools import cached_property

from module.base.timer import Timer
from module.logger import logger
from module.outpost.assets import *
from module.ui.assets import CLICK_TO_NEXT, COMMISSION_CHECK, SELECT_MAX
from module.ui.page import page_recycling
from module.ui.ui import UI


class NoEnoughItems(Exception):
    pass


class Recycling(UI):
    diff = datetime.now(timezone.utc).astimezone().utcoffset() - timedelta(hours=8)

    @cached_property
    def next_tuesday(self) -> datetime:
        local_now = datetime.now()
        remain = (1 - local_now.weekday()) % 7
        remain = remain + 7 if remain == 0 else remain
        return local_now.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=remain) + self.diff

    def upgrade(self):
        logger.hr('Recycling special upgrade', 2)
        click_timer = Timer(0.3)

        while 1:
            self.device.screenshot()

            # 全部领取
            if click_timer.reached() and self.appear_then_click(CLAIM, threshold=20, interval=1):
                click_timer.reset()
                continue

            # 等待领取完毕
            if self.appear(COMMISSION_CHECK, offset=10) and self.appear(CLAIM_DONE, threshold=20):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    # 需要派遣
                    if self.appear(DISPATCH, threshold=10):
                        logger.info('Receive commission done')
                        break
                    # 没次数/进行中
                    if self.appear(DISPATCH_DONE, threshold=10):
                        raise NoOpportunity
            else:
                confirm_timer.clear()

    def upgrade_common(self):
        logger.hr('Recycling common upgrade', 2)
        click_timer = Timer(0.3)

        max_click = 0
        while 1:
            self.device.screenshot()

            # 通用研究
            if click_timer.reached() and self.appear_then_click(SYNCHRO_COMMON_UPGRADE, offset=30, interval=1):
                click_timer.reset()
                continue

            # 点击进行下一步
            if click_timer.reached() and self.appear_then_click(CLICK_TO_NEXT, offset=30, interval=1, static=False):
                click_timer.reset()
                continue

            # 点击MAX
            if (
                max_click < 2
                and click_timer.reached()
                and self.appear_then_click(SELECT_MAX, offset=30, interval=1, static=False)
            ):
                max_click += 1
                click_timer.reset()
                continue

            # 不能升级
            if (
                max_click > 1
                and self.appear(RECYCLING_UPGRADE_CHECK, offset=50)
                and not self.appear(SYNCHRO_COMMON_UPGRADE_CONFIRM, threshold=10)
            ):
                logger.info('Recycling common upgrade done')
                break

        # 关闭弹窗
        while 1:
            self.device.screenshot()

            # 通用研究
            if (
                click_timer.reached()
                and self.appear(RECYCLING_UPGRADE_CHECK, offset=50)
                and self.appear_then_click(SYNCHRO_UPGRADE_CLOSE, offset=30, interval=1, static=False)
            ):
                click_timer.reset()
                continue

            # 返回
            if click_timer.reached() and self.appear(SYNCHRO_COMMON_UPGRADE, offset=30):
                logger.info('Back to recycling room')
                break

    def run(self):
        logger.hr('Recycling upgrade')
        self.ui_ensure(page_recycling)

        self.upgrade_common()

        try:
            self.upgrade()
        except NoEnoughItems:
            logger.info('No enough items left, upgrade done')

        self.config.task_delay(server_update=True)
