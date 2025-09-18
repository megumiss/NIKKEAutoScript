from module.base.timer import Timer
from module.daemon.daemon_base import DaemonBase
from module.event.assets import FIELD_CHANGE
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING, FIGHT, PAUSE
from module.tribe_tower.assets import NEXT_STAGE
from module.ui.assets import FIGHT_QUICKLY_ENABLE, SKIP
from module.ui.ui import UI


class SemiCombat(UI, DaemonBase):
    def run(self):
        timeout = Timer(600, count=10)
        click_timer = Timer(0.9)

        while 1:
            self.device.screenshot()

            # 快速战斗
            if (
                self.config.SemiCombat_FightQuickly
                and click_timer.reached()
                and self.appear_then_click(FIGHT_QUICKLY_ENABLE, threshold=20, interval=2)
            ):
                click_timer.reset()
                continue

            # 进入战斗
            if click_timer.reached() and self.appear_then_click(FIGHT, threshold=20, interval=2):
                click_timer.reset()
                continue

            # 跳过剧情
            if (
                self.config.SemiCombat_SkipStory
                and click_timer.reached()
                and self.appear_then_click(SKIP, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 下一关卡
            if click_timer.reached() and self.appear_then_click(NEXT_STAGE, offset=(100, 30), interval=2):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(END_FIGHTING, offset=30):
                click_timer.reset()
                continue

            # 前往区域
            if click_timer.reached() and self.appear_then_click(FIELD_CHANGE, offset=30, interval=1):
                click_timer.reset()
                continue

            # 自动射击
            if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=10, threshold=0.9, interval=5):
                click_timer.reset()
                continue
            if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=10, threshold=0.9, interval=5):
                click_timer.reset()
                continue

            # 红圈
            if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=10):
                if self.handle_red_circles():
                    click_timer.reset()
                    continue

            if not timeout.started():
                timeout.start()
            if timeout.reached():
                break
            else:
                timeout.clear()


if __name__ == '__main__':
    b = SemiCombat('nkas', task='SemiCombat')
    b.run()
