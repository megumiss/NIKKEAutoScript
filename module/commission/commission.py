from module.base.timer import Timer
from module.commission.assets import *
from module.logger import logger
from module.ui.assets import COMMISSION_CHECK
from module.ui.page import page_commission
from module.ui.ui import UI


class Commission(UI):
    def dispatch_and_claim(self, skip_first_screenshot=True):
        logger.hr('Dispatch and claim commission')
        confirm_timer = Timer(8, count=3).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.handle_reward(interval=1):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and (
                self.appear_then_click(DISPATCH, offset=(30, 30), interval=2)
                or self.appear_then_click(DISPATCH_CONFIRM, offset=(30, 30), interval=2)
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and (
                self.appear_then_click(CLAIM, offset=(30, 30), interval=2)
                or self.appear_then_click(DISPATCH_CONFIRM, offset=(30, 30), interval=2)
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(COMMISSION_CHECK, offset=(10, 10)) and confirm_timer.reached():
                break

    def run(self):
        self.ui_ensure(page_commission)
        self.dispatch_and_claim()
        self.config.task_delay(server_update=True)
