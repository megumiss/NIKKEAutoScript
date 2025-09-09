from module.base.timer import Timer
from module.event_daemon.assets import BATTLE_QUICKLY, FIGHT_2, SKIP
from module.shop.assets import MAX
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING, FIGHT
from module.tribe_tower.assets import NEXT_STAGE
from module.ui.ui import UI


class EventDaemon(UI):
    def run(self):
        # self.ui_goto_main()
        timeout = Timer(600, count=3).start()
        confirm_timer = Timer(2, count=3).start()
        click_timer = Timer(0.9)
        # skip_first_screenshot = True
        # while 1:
        #     if skip_first_screenshot:
        #         skip_first_screenshot = False
        #     else:
        #         self.device.screenshot()
        #
        #     if click_timer.reached():
        #         self.device.click_minitouch(530, 1080)
        #         click_timer.reset()
        #         confirm_timer.reset()
        #         timeout.reset()
        #         continue
        #
        #     if self.appear(GOTO_MAIN, 5) and confirm_timer.reached():
        #         break

        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
                self.device.stuck_record_clear()
                self.device.click_record_clear()

            if click_timer.reached() and self.appear_then_click(MAX, 5, interval=2, threshold=0.8, static=False):
                click_timer.reset()
                confirm_timer.reset()
                timeout.reset()
                continue

            if click_timer.reached() and self.appear_then_click(FIGHT_2, 5, interval=2, static=False):
                click_timer.reset()
                confirm_timer.reset()
                timeout.reset()
                continue

            if (
                click_timer.reached()
                and self.appear_then_click(BATTLE_QUICKLY, 5, interval=4, static=False)
                and BATTLE_QUICKLY.match_appear_on(self.device.image)
            ):
                click_timer.reset()
                confirm_timer.reset()
                timeout.reset()
                continue

            if (
                click_timer.reached()
                and self.appear_then_click(FIGHT, 5, interval=4, static=False)
                and FIGHT.match_appear_on(self.device.image)
            ):
                click_timer.reset()
                confirm_timer.reset()
                timeout.reset()
                continue

            if click_timer.reached() and self.appear_then_click(SKIP, 5):
                click_timer.reset()
                confirm_timer.reset()
                timeout.reset()
                continue

            if click_timer.reached() and self.appear_then_click(NEXT_STAGE, 5, static=False):
                click_timer.reset()
                confirm_timer.reset()
                timeout.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=5, threshold=0.9, interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=5, threshold=0.9, interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(END_FIGHTING, offset=30):
                while 1:
                    self.device.screenshot()
                    if not self.appear(END_FIGHTING, offset=30):
                        click_timer.reset()
                        break
                    if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                        click_timer.reset()
                        continue
                click_timer.reset()
                confirm_timer.reset()
                timeout.reset()
                continue

            if timeout.reached():
                break

        self.config.task_delay(server_update=True)
