from functools import cached_property

from module.base.timer import Timer
from module.base.utils import point2str
from module.exception import OperationFailed
from module.logger import logger
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, PAUSE
from module.tribe_tower.assets import *
from module.ui.assets import GOTO_BACK, MAIN_CHECK, TRIBE_TOWER_CHECK
from module.ui.page import page_surface
from module.ui.ui import UI


class NoOpportunityRemain(Exception):
    pass


class NoSquadsValid(Exception):
    pass


class SurfaceDaily(UI):
    @cached_property
    def squad_list(self) -> list:
        """
        第一页所有可用的队伍
        """
        squads = []
        for index in 5:
            squads.append(index)
            if option in self.BIOS_SETTING_RATIO_MAP:
                total += self.BIOS_SETTING_RATIO_MAP[option]
            else:
                logger.warning(f"Unknown BIOS setting option: '{option}', using default level 1")
                total += 1
        return squads

    def _run(self):
        try:
            # 检查队伍
            self.check_squad()
            # 检查任务
            self.open_mission_board()

        except NoOpportunityRemain:
            self.close_mission_board()
            logger.warning('The current tribe tower has no remaining opportunities')
        except NoSquadsValid:
            logger.warning('The current tribe tower has no remaining opportunities')

    def check_squad(self, skip_first_screenshot=True):
        logger.info('Open squad list')

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear(MISSION_REMAIN_0, offset=10):
                squads = self.squad_list
                if len(squads) >= 3:
                    return squads
                else:
                    raise NoSquadsValid

            if self.appear_then_click(QUEST, offset=(30, 30), interval=1):
                continue

    def open_mission_board(self, skip_first_screenshot=True):
        logger.info('Open mission board')

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear(MISSION_REMAIN_0, offset=10):
                raise NoOpportunityRemain

            if self.appear_then_click(QUEST, offset=(30, 30), interval=1):
                continue

            if (
                self.appear(SURFACE_MISSION_CHECK, offset=10)
                and self.appear(SURFACE_MISSION_LOAD_CHECK, offset=10)
                and not self.appear(MISSION_REMAIN_0, offset=10)
            ):
                logger.info('Opened mission board')
                break

    def close_mission_board(self):
        logger.info('Close mission board')

        while 1:
            self.device.screenshot()

            if self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear_then_click(
                SURFACE_MISSION_CLOSE, offset=(30, 30), interval=1
            ):
                continue

            if not self.appear(SURFACE_MISSION_CHECK, offset=10):
                logger.info('Closed mission board')
                break

    def try_to_overcome_current_stage(self, skip_first_screenshot=True):
        logger.hr('OVERCOME STAGE', 3)
        confirm_timer = Timer(1, count=2).start()
        click_timer = Timer(0.3)
        try:
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if click_timer.reached() and self.handle_paid_gift():
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear(FIGHT, offset=5, interval=5):
                    if FIGHT.match_appear_on(self.device.image, 10):
                        self.device.click(FIGHT)
                        confirm_timer.reset()
                        click_timer.reset()
                        continue

                    raise NoOpportunityRemain

                if click_timer.reached() and self.appear_then_click(
                    AUTO_SHOOT, offset=(30, 30), threshold=0.9, interval=5
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if click_timer.reached() and self.appear_then_click(
                    AUTO_BURST, offset=(30, 30), threshold=0.9, interval=5
                ):
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                # 红圈
                if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                    if self.handle_red_circles():
                        continue

                if click_timer.reached() and self.appear_then_click(NEXT_STAGE, offset=(30, 30)):
                    self.device.sleep(5)
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if (
                    click_timer.reached()
                    and not self.appear(NEXT_STAGE, offset=(30, 30))
                    and self.appear(END_CHECK, offset=30)
                ):
                    while 1:
                        self.device.screenshot()
                        if not self.appear(END_CHECK, offset=30):
                            click_timer.reset()
                            break
                        if self.appear_then_click(END_CHECK, offset=30, interval=1):
                            click_timer.reset()
                            continue
                    confirm_timer.reset()
                    click_timer.reset()
                    continue

                if self.appear(OPERATION_FAILED, offset=(30, 30)):
                    raise OperationFailed

                if click_timer.reached() and self.appear(PAUSE, offset=(30, 30)):
                    if self.config.Overcome_OnlyToCompleteDailyMission:
                        self.ensure_failed()
                    confirm_timer.reset()
                    click_timer.reset()
                    self.device.sleep(5)
                    continue

                if (
                    self.appear(TRIBE_TOWER_DETAILED_CHECK, offset=(30, 30), interval=6)
                    and self.appear(GOTO_BACK, offset=(30, 30))
                    and confirm_timer.reached()
                ):
                    raise NoOpportunityRemain

        except OperationFailed:
            if not self.config.Overcome_OnlyToCompleteDailyMission:
                logger.warning('failed to overcome the current stage, will try the other tribe tower')
                self.available_company.remove(self.available_company[0])
            else:
                self.available_company.clear()
            self.ensure_back()
            return

    def ensure_failed(self, skip_first_screenshot=True):
        logger.info("abandon the current attempt to overcome it, because it's set up this way")
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() and self.appear_then_click(PAUSE, offset=(30, 30), interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(ABANDON, offset=(30, 30), interval=5):
                click_timer.reset()
                continue

            if self.appear(OPERATION_FAILED, offset=(30, 30)):
                raise OperationFailed

    def run(self):
        self.ui_ensure(page_surface)
        self._run()
        self.config.task_delay(server_update=True)
