from module.base.timer import Timer
from module.base.utils import point2str
from module.exception import OperationFailed
from module.logger import logger
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, PAUSE
from module.tribe_tower.assets import *
from module.ui.assets import GOTO_BACK, MAIN_CHECK, TRIBE_TOWER_CHECK
from module.ui.page import page_tribe_tower
from module.ui.ui import UI


class TowerDaemon(UI):
    company = []

    def _run(self):
        self.ensure_into_tribe_tower()

    def ensure_into_tribe_tower(self, skip_first_screenshot=True):
        logger.hr('TRIBE TOWER START')
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.handle_paid_gift():
                click_timer.reset()
                continue

            if (
                click_timer.reached()
                and self.appear(TRIBE_TOWER_CHECK, offset=10, interval=5)
                and self.appear_then_click(TRIBE_CHECK, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            if self.appear(TRIBE_TOWER_DETAILED_CHECK, offset=10):
                break

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.handle_paid_gift():
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(TRIBE_TOWER_DETAILED_CHECK, offset=10, interval=5):
                self.device.click_minitouch(360, 560)
                logger.info('Click %s @ %s' % (point2str(360, 560), 'STAGE'))
                click_timer.reset()
                continue

            if self.appear(STAGE_INFO_CHECK, offset=10, static=False):
                break

        self.device.stuck_record_clear()
        self.device.click_record_clear()
        self.try_to_current_stage()

    def try_to_current_stage(self, skip_first_screenshot=True):
        logger.hr('CURRENT STAGE', 3)
        click_timer = Timer(0.3)
        try:
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if click_timer.reached() and self.handle_paid_gift():
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear_then_click(FIGHT, offset=30, interval=1, threshold=0.8):
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=10, threshold=0.9, interval=5):
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=10, threshold=0.9, interval=5):
                    click_timer.reset()
                    continue

                # 红圈
                if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=10):
                    if self.handle_red_circles():
                        continue

                if click_timer.reached() and self.appear_then_click(NEXT_STAGE, offset=10):
                    self.device.sleep(5)
                    click_timer.reset()
                    continue

                if (
                    click_timer.reached()
                    and not self.appear(NEXT_STAGE, offset=10)
                    and self.appear_then_click(END_CHECK, offset=10)
                ):
                    click_timer.reset()
                    continue

                if self.appear(OPERATION_FAILED, offset=10):
                    raise OperationFailed

        except OperationFailed:
            logger.warning('failed to the current stage')
            self.ensure_back()
            return

    def ensure_back(self, skip_first_screenshot=True):
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.handle_paid_gift():
                click_timer.reset()
                continue

            if self.appear(TRIBE_TOWER_CHECK, offset=5) or self.appear(MAIN_CHECK, offset=5, static=False):
                return

            if click_timer.reached() and self.appear_then_click(BACK, offset=(5, 5), interval=6):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(GOTO_BACK, offset=(30, 30), interval=6):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(FIGHT, offset=5):
                self.device.click_minitouch(1, 1)
                click_timer.reset()
                continue

    def run(self):
        self.ui_ensure(page_tribe_tower)
        self._run()
        self.config.task_delay(server_update=True)
