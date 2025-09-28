from datetime import datetime, timedelta, timezone
from functools import cached_property

from module.base.button import merge_buttons
from module.base.timer import Timer
from module.logger import logger
from module.outpost.assets import *
from module.ui.assets import CLICK_TO_NEXT, SELECT_MAX
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

        # 获取所有level并去重
        levels = TEMPLATE_RECYCLING_LEVEL.match_multi(self.device.image, threshold=0.65, name='RECYCLING_LEVEL')
        levels = merge_buttons(levels, x_threshold=30, y_threshold=30)

        for level in levels:
            if level.location[1] > 500 and level.location[1] < 550:
                logger.info('Skip common upgrade')
                continue
            logger.hr('Recycling special upgrade', 3)

            # 打开某个企业/职业
            while 1:
                self.device.screenshot()

                if click_timer.reached() and self.appear(RECYCLING_COMMON_UPGRADE, offset=30):
                    self.device.click(level, click_offset=(0, 100))
                    self.device.sleep(0.5)
                    continue

                if self.appear(RECYCLING_UPGRADE_CHECK, offset=30, static=False):
                    logger.info('Open upgrade window')
                    break

            # 升级
            auto_click = 0
            while 1:
                self.device.screenshot()

                # 点击进行下一步
                if click_timer.reached() and self.appear_then_click(CLICK_TO_NEXT, offset=30, interval=1, static=False):
                    click_timer.reset()
                    continue

                # 自动选择
                if (
                    auto_click < 5
                    and click_timer.reached()
                    and self.appear(RECYCLING_UPGRADE_CHECK, offset=30, static=False)
                    and self.appear_then_click(SYNCHRO_UPGRADE_AUTO, offset=30, static=False)
                ):
                    auto_click += 1
                    click_timer.reset()
                    continue

                # 升级
                if (
                    auto_click > 4
                    and click_timer.reached()
                    and self.appear(RECYCLING_UPGRADE_CHECK, offset=30, static=False)
                    and self.appear_then_click(RECYCLING_UPGRADE_CONFIRM, offset=30, interval=1)
                ):
                    auto_click = 0
                    self.device.sleep(2)
                    self.device.click_record_clear()
                    click_timer.reset()
                    continue

                # 材料不够,关闭窗口
                if (
                    auto_click > 4
                    and self.appear(RECYCLING_UPGRADE_CHECK, offset=30, static=False)
                    and not self.appear(RECYCLING_UPGRADE_CONFIRM, threshold=10)
                    and self.appear_then_click(RECYCLING_UPGRADE_CLOSE, offset=30, interval=1, static=False)
                ):
                    self.device.click_record_clear()
                    click_timer.reset()
                    continue

                if self.appear(RECYCLING_COMMON_UPGRADE, offset=30):
                    logger.info('Upgrade done')
                    break

    def upgrade_common(self):
        logger.hr('Recycling common upgrade', 2)
        click_timer = Timer(0.3)

        max_click = 0
        while 1:
            self.device.screenshot()

            # 通用研究
            if click_timer.reached() and self.appear_then_click(RECYCLING_COMMON_UPGRADE, offset=30, interval=1):
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

            if (
                max_click > 1
                and click_timer.reached()
                and self.appear_then_click(RECYCLING_COMMON_UPGRADE_CONFIRM, threshold=10)
            ):
                click_timer.reset()
                continue

            # 不能升级
            if (
                max_click > 1
                and self.appear(RECYCLING_UPGRADE_CHECK, offset=50)
                and not self.appear(RECYCLING_COMMON_UPGRADE_CONFIRM, threshold=10)
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
                and self.appear_then_click(RECYCLING_UPGRADE_CLOSE, offset=30, interval=1, static=False)
            ):
                click_timer.reset()
                continue

            # 返回
            if click_timer.reached() and self.appear(RECYCLING_COMMON_UPGRADE, offset=30):
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
