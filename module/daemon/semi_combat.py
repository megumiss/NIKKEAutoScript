from module.base.timer import Timer
from module.daemon.assets import MAIN_STORY_MAP_CLOSE, MAIN_STORY_MARK_IN, MAIN_STORY_MARK_OUT, MAIN_STORY_NORMAL
from module.daemon.daemon_base import DaemonBase
from module.event.assets import FIELD_CHANGE
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING, FIGHT, PAUSE
from module.tribe_tower.assets import NEXT_STAGE
from module.ui.assets import FIGHT_QUICKLY_ENABLE, SKIP
from module.ui.ui import UI


class SemiCombat(UI, DaemonBase):
    def run(self):
        timeout = Timer(600, count=10)
        click_timer = Timer(0.3)

        while 1:
            self.device.screenshot()

            # 关闭地图
            if (
                self.config.SemiCombat_MainStoryMark
                and click_timer.reached()
                and self.appear_then_click(MAIN_STORY_MAP_CLOSE, offset=30, interval=1)
            ):
                click_timer.reset()
                continue

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

            # 主线剧情图标，界面外
            if (
                self.config.SemiCombat_MainStoryMark
                and click_timer.reached()
                and self.appear(MAIN_STORY_NORMAL, offset=30)
                and self.appear_then_click(MAIN_STORY_MARK_OUT, offset=30, threshold=0.85, interval=5, static=False)
            ):
                click_timer.reset()
                continue

            # 主线剧情图标，界面内
            if (
                self.config.SemiCombat_MainStoryMark
                and click_timer.reached()
                and not self.appear(FIGHT_QUICKLY_ENABLE, threshold=20)
                and not self.appear(FIGHT, threshold=20)
                and self.appear(MAIN_STORY_NORMAL, offset=30)
                and self.appear_with_scale_then_click(
                    MAIN_STORY_MARK_IN, click_offset=(0, 130), scale_range=(0.7, 1.2), interval=5
                )
            ):
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
