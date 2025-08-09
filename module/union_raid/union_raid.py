from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Digit
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING, PAUSE
from module.ui.assets import MAIN_CHECK
from module.ui.page import page_main
from module.ui.ui import UI
from module.union_raid.assets import *


class NoOpportunityRemain(Exception):
    pass


class UnionRaidIsUnavailable(Exception):
    pass


class UnionRaid(UI):
    @property
    def free_remain(self) -> int:
        model_type = self.config.Optimization_OcrModelType
        FREE_REMAIN = Digit(
            [FREE_OPPORTUNITY_CHECK.area],
            name='FREE_REMAIN',
            model_type=model_type,
            lang='ch',
        )
        return int(FREE_REMAIN.ocr(self.device.image)['text'])

    @property
    def free_opportunity_remain(self) -> bool:
        if self.free_remain:
            logger.info(f'[Free opportunities remain] {self.free_remain}')
        return self.free_remain

    def ensure_into_union(self, skip_first_screenshot=True):
        """进入联盟"""
        logger.hr('UNION RAID START')
        click_timer = Timer(0.3)
        confirm_timer = Timer(10, count=10).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(MAIN_CHECK, offset=10)
                and self.appear_then_click(UNION, offset=10, interval=3)
            ):
                click_timer.reset()
                continue

            # 联盟主页
            if self.appear(UNION_CHECK, offset=10):
                logger.info('Enter union')
                break

            if confirm_timer.reached():
                logger.error('Union raid has end')
                raise UnionRaidIsUnavailable

    def ensure_into_unionraid(self, skip_first_screenshot=True):
        """进入联盟突袭"""
        click_timer = Timer(0.3)
        confirm_timer = Timer(3, count=3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(UNION_CHECK, offset=10)
                and self.appear_then_click(UNION_RAID, offset=10, interval=3)
            ):
                click_timer.reset()
                continue

            # 结算弹窗
            if (
                click_timer.reached()
                and self.appear(ENEMY_DEFEATED, offset=10)
                and self.appear_then_click(ENEMY_DEFEATED_CONFIRM, offset=(200, 10), interval=2)
            ):
                click_timer.reset()
                continue

            # 突袭主页
            if self.appear(UNION_RAID_CHECK, offset=10):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    logger.info('Enter union raid')
                    self.device.sleep(1)
                    break
            else:
                confirm_timer.clear()

        if self.free_opportunity_remain:
            self.union_raid()
        else:
            logger.warning('There are no free opportunities')
            raise NoOpportunityRemain

    def union_raid(self, skip_first_screenshot=True):
        logger.hr('Start a union raid')
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 点击莱彻
            if (
                click_timer.reached()
                and self.appear(UNION_RAID_CHECK, offset=10)
                and self.appear_then_click(UNION_RAID_SELECT, offset=(50, 10), interval=2)
            ):
                click_timer.reset()
                continue

            # 切换队伍2
            if (
                click_timer.reached()
                and self.appear(UNION_RAID_ENEMY_CHECK, offset=10)
                and self.appear(RAID_TEAM_1_SELECTED, threshold=10)
                and self.appear(RAID_TEAM_LOCKED, offset=10)
                and self.appear_then_click(RAID_TEAM_2, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 切换队伍3
            if (
                click_timer.reached()
                and self.appear(UNION_RAID_ENEMY_CHECK, offset=10)
                and self.appear(RAID_TEAM_2_SELECTED, threshold=10)
                and self.appear(RAID_TEAM_LOCKED, offset=10)
                and self.appear_then_click(RAID_TEAM_3, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 进入战斗
            if (
                click_timer.reached()
                and self.appear(UNION_RAID_ENEMY_CHECK, offset=10)
                and self.appear_then_click(ENTER_FIGHT, offset=10, interval=2)
            ):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=10, threshold=0.9, interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=10, interval=5):
                click_timer.reset()
                continue

            # 红圈
            if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                if self.handle_red_circles():
                    continue

            # 战斗结束
            if click_timer.reached() and self.appear_then_click(END_FIGHTING, offset=10, interval=1):
                logger.info('Complete a union raid')
                break

        confirm_timer = Timer(3, count=3)
        while 1:
            self.device.screenshot()

            # 结算弹窗
            if (
                click_timer.reached()
                and self.appear(ENEMY_DEFEATED, offset=10)
                and self.appear_then_click(ENEMY_DEFEATED_CONFIRM, offset=(200, 10), interval=2)
            ):
                click_timer.reset()
                continue

            # 突袭主页
            if self.appear(UNION_RAID_CHECK, offset=10):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    self.device.sleep(1)
                    break
            else:
                confirm_timer.clear()

        if self.free_opportunity_remain:
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.union_raid()
        else:
            logger.info('There are no free opportunities')
            raise NoOpportunityRemain

    def run(self):
        try:
            self.ui_ensure(page_main)
            self.ensure_into_union()
            self.ensure_into_unionraid()
        except UnionRaidIsUnavailable:
            pass
        except NoOpportunityRemain:
            pass

        self.config.task_delay(server_update=True)
