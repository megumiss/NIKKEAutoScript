from module.daemon.daemon_base import DaemonBase
from module.interception.assets import SIMULATION_BATTLE
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING, PAUSE
from module.ui.ui import UI


class InterceptionTest(UI, DaemonBase):
    def run(self):
        while 1:
            self.device.screenshot()

            if self.appear(END_FIGHTING, offset=30):
                break

            if self.appear_then_click(SIMULATION_BATTLE, offset=30, interval=1):
                continue

            if self.appear_then_click(AUTO_SHOOT, offset=(5, 5), threshold=0.9, interval=5):
                continue

            if self.appear_then_click(AUTO_BURST, offset=(5, 5), threshold=0.9, interval=5):
                continue

            if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                if self.handle_red_circles():
                    continue


if __name__ == '__main__':
    b = InterceptionTest('nkas', task='InterceptionTest')
    b.run()
