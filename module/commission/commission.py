from module.base.timer import Timer
from module.commission.assets import *
from module.logger import logger
from module.ui.assets import COMMISSION_CHECK
from module.ui.page import page_commission
from module.ui.ui import UI


class NoOpportunity(Exception):
    pass


class Commission(UI):
    def receive(self, skip_first_screenshot=True):
        logger.hr('Receive commission', 1)
        confirm_timer = Timer(2, count=3)
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 没次数
            if self.appear(COMMISSION_CHECK, offset=10) and self.appear(CLAIM_DONE, offset=10):
                raise NoOpportunity

            if self.handle_reward(interval=1):
                click_timer.reset()
                continue

            # 全部领取
            if click_timer.reached() and (self.appear_then_click(CLAIM, offset=10, interval=1)):
                click_timer.reset()
                continue

            # 派遣弹窗和派遣按钮
            if self.appear(COMMISSION_CHECK, offset=10) and self.appear(DISPATCH, threshold=10):
                logger.info('Receive commission done')
                break

            # if self.appear(COMMISSION_CHECK, offset=10):
            #     if not confirm_timer.started():
            #         confirm_timer.start()
            #     if confirm_timer.reached():
            #         logger.info('Receive commission done')
            #         break
            # else:
            #     confirm_timer.clear()

    def select_favorite():
        logger.hr('Select favorite item', 1)

    def dispatch(self, skip_first_screenshot=True):
        logger.hr('Dispatch commission', 1)
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 派遣
            if (
                click_timer.reached()
                and self.appear(COMMISSION_CHECK, offset=10)
                and self.appear_then_click(DISPATCH, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 派遣确认
            if (
                click_timer.reached()
                and self.appear(ITEM_LIST_CHECK, offset=10)  # 复用
                and self.appear_then_click(DISPATCH_CONFIRM, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            if self.appear(COMMISSION_CHECK, offset=(10, 10)):
                logger.info('Dispatch commission done', 1)
                break

    def run(self):
        logger.hr('Dispatch and claim commission')
        self.ui_ensure(page_commission)
        try:
            # 领取
            self.receive()
            # 处理收藏品
            if self.config.CollectionItems_Enable and not self.config.CollectionItems_NIKKE:
                self.select_favorite()
            # 派遣
            self.dispatch()
        except NoOpportunity:
            logger.warning('Commission allready done')

        self.config.task_delay(server_update=True)
