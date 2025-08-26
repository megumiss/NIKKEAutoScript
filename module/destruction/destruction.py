from module.base.timer import Timer
from module.destruction.assets import DESTROY
from module.logger import logger
from module.ui.assets import DESTROY_CHECK
from module.ui.page import page_destroy
from module.ui.ui import UI


class Destruction(UI):
    def destroy(self, skip_first_screenshot=True):
        logger.hr('Destruction')
        confirm_timer = Timer(3, count=3).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear_then_click(DESTROY, offset=(30, 30), interval=3, static=False):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.handle_level_up():
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.handle_reward(interval=1):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.handle_paid_gift():
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(DESTROY_CHECK, offset=(10, 10)) and confirm_timer.reached():
                break

        logger.info('Destruction has finished')
        return True

    def run(self):
        self.ui_ensure(page_destroy)
        self.destroy()
        self.config.task_delay(server_update=True)
