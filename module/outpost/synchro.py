from datetime import datetime, timedelta, timezone
from functools import cached_property

from module.base.timer import Timer
from module.logger import logger
from module.outpost.assets import *
from module.ui.page import page_synchro
from module.ui.ui import UI


class NoEnoughItems(Exception):
    pass


class Synchro(UI):
    diff = datetime.now(timezone.utc).astimezone().utcoffset() - timedelta(hours=8)

    @cached_property
    def next_tuesday(self) -> datetime:
        local_now = datetime.now()
        remain = (1 - local_now.weekday()) % 7
        remain = remain + 7 if remain == 0 else remain
        return local_now.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=remain) + self.diff

    def upgrade(self):
        logger.info('Open synchro upgrade window')
        click_timer = Timer(0.3)

        while 1:
            self.device.screenshot()

            # 使用道具
            if (
                self.config.Synchro_UseBox
                and click_timer.reached()
                and self.appear(SYNCHRO_UPGRADE_CHECK, offset=30)
                and self.appear_then_click(SYNCHRO_UPGRADE_BOX, offset=30, interval=1)
            ):
                click_timer.reset()
                continue

            # 增强
            if click_timer.reached() and self.appear_then_click(SYNCHRO_UPGRADE, offset=30, interval=5):
                auto_click = 0
                self.device.sleep(0.5)
                click_timer.reset()
                continue

            # 自动选择
            if (
                auto_click < 2
                and click_timer.reached()
                and self.appear(SYNCHRO_UPGRADE_CHECK, offset=30)
                and self.appear_then_click(SYNCHRO_UPGRADE_AUTO, offset=30, interval=1)
            ):
                auto_click += 1
                click_timer.reset()
                continue

            # 没材料
            if self.appear(SYNCHRO_UPGRADE_CHECK, offset=30):
                if not self.config.Synchro_UseBox:
                    # 不使用Box时，只要确认按钮不出现就退出
                    if auto_click > 1 and not self.appear(SYNCHRO_UPGRADE_CONFIRM, threshold=10):
                        raise NoEnoughItems
                else:
                    # 使用Box时，确认按钮和Box都不出现才退出
                    if (
                        auto_click > 1
                        and not self.appear(SYNCHRO_UPGRADE_BOX, offset=30)
                        and not self.appear(SYNCHRO_UPGRADE_CONFIRM, threshold=10)
                    ):
                        raise NoEnoughItems

            # 开始增强
            if (
                click_timer.reached()
                and self.appear(SYNCHRO_UPGRADE_CHECK, offset=30)
                and self.appear_then_click(SYNCHRO_UPGRADE_CONFIRM, offset=30, interval=1)
            ):
                click_timer.reset()
                continue

            # 升级完成弹窗
            if click_timer.reached() and self.appear_then_click(SYNCHRO_UPGRADE_DONE, offset=30, interval=1):
                click_timer.reset()
                continue

    def run(self):
        logger.hr('Synchro upgrade')
        self.ui_ensure(page_synchro)
        try:
            self.upgrade()
        except NoEnoughItems:
            logger.info('No enough items left, upgrade done')

        self.config.task_delay(target=self.next_tuesday)
