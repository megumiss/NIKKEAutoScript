from module.base.timer import Timer
from module.logger import logger
from module.mailbox.assets import RECEIVE
from module.ui.assets import MAIN_GOTO_MAILBOX
from module.ui.page import page_mailbox, page_main
from module.ui.ui import UI


class Mailbox(UI):
    def check(self, skip_first_screenshot=True):
        logger.hr('Check the mailbox')
        confirm_timer = Timer(3, count=3).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if RECEIVE.appear_on(self.device.image):
                self.device.click_minitouch(*RECEIVE.location)
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.handle_reward(interval=1):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(page_mailbox.check_button, offset=(10, 10)) and confirm_timer.reached():
                break

    def run(self):
        self.ui_ensure(page_main)
        if MAIN_GOTO_MAILBOX.match(self.device.image, threshold=0.74):
            self.ui_ensure(page_mailbox)
            self.check()
        else:
            logger.info('The mailbox has no letters that need to be checked.')
        self.config.task_delay(server_update=True)
