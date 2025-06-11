from module.base.timer import Timer
from module.interception.assets import *
from module.simulation_room.assets import END_FIGHTING, AUTO_SHOOT, AUTO_BURST, PAUSE
from module.ui.page import page_interception
from module.ui.ui import UI


class NoOpportunity(Exception):
    pass


class Interception(UI):
    def _run(self, skip_first_screenshot=True):
        confirm_timer = Timer(1.2, count=2).start()
        click_timer = Timer(0.7)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and KRAKEN_CHECK.match(self.device.image):
                self.device.click_minitouch(360, 1030)
                click_timer.reset()
                confirm_timer.reset()

            elif click_timer.reached() and self.appear_then_click(KRAKEN, offset=5):
                click_timer.reset()
                confirm_timer.reset()

            if ABNORMAL_INTERCEPTION_CHECK.match(self.device.image) and confirm_timer.reached():
                break

        skip_first_screenshot = True
        confirm_timer.reset()
        click_timer.reset()
        self.device.click_record_clear()
        self.device.stuck_record_clear()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() \
                    and BATTLE_QUICKLY.match_appear_on(self.device.image, 10) \
                    and self.appear_then_click(BATTLE_QUICKLY, offset=5):
                click_timer.reset()
                confirm_timer.reset()
                continue

            elif click_timer.reached() \
                    and BATTLE.match_appear_on(self.device.image, 10) \
                    and self.appear_then_click(BATTLE, offset=5):
                click_timer.reset()
                confirm_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=(5, 5), interval=5, threshold=0.8):
                click_timer.reset()
                confirm_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=(5, 5), interval=5, threshold=0.8):
                click_timer.reset()
                confirm_timer.reset()
                continue
            
            # 红圈
            if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                if self.handle_red_circles():
                    continue
            
            if click_timer.reached() and self.appear_then_click(END_FIGHTING, offset=(5, 5), interval=2):
                click_timer.reset()
                confirm_timer.reset()
                continue

            if self.appear(ABNORMAL_INTERCEPTION_CHECK, offset=5, interval=2) and not BATTLE.match_appear_on(
                    self.device.image, 10) and confirm_timer.reached():
                raise NoOpportunity

    def run(self):
        self.ui_ensure(page_interception)
        try:
            self._run()
        except NoOpportunity:
            pass
        self.config.task_delay(server_update=True)
