from module.base.timer import Timer
from module.liberation.assets import *
from module.ui.assets import GOTO_BACK, TEAM_GOTO_LIBERATION
from module.ui.page import page_team
from module.ui.ui import UI


class Liberation(UI):
    def _run(self, skip_first_screenshot=True):
        confirm_timer = Timer(10, count=10).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(TEAM_GOTO_LIBERATION, offset=(30, 30), interval=2):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                COMPLETED_1, offset=(30, 30), interval=1, threshold=0.8, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                COMPLETED_2, offset=(30, 30), interval=1, threshold=0.8, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                COMPLETED_3, offset=(30, 30), interval=1, threshold=0.8, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                COMPLETED_4, offset=(30, 30), interval=1, threshold=0.8, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                COMPLETED_5, offset=(30, 30), interval=1, threshold=0.8, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                COMPLETED_6, offset=(30, 30), interval=1, threshold=0.8, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                CONFIRM_D, offset=(30, 30), interval=1, threshold=0.8, static=False
            ):
                self.config.modified[f'Liberation.Scheduler.Enable'] = False
                confirm_timer.reset()
                click_timer.reset()
                continue

            # if click_timer.reached() and self.handle_event(1):
            #     confirm_timer.reset()
            #     click_timer.reset()
            #     continue

            if click_timer.reached() and self.handle_reward(1):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(GOTO_BACK, offset=(30, 30)) and confirm_timer.reached():
                break

    def run(self):
        self.ui_ensure(page_team, skip_first_screenshot=True)
        self._run()
        self.config.task_delay(server_update=True)
