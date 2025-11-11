from module.logger import logger
from module.surface.assets import *
from module.ui.page import page_surface
from module.ui.ui import UI


class NoOpportunityRemain(Exception):
    pass


class IsSquadValid(Exception):
    pass


class SurfaceDaily(UI):
    SQUAD_LISTS = {'SQUAD_1': SQUAD_1_IN_HOSPITAL, 'SQUAD_2': SQUAD_2_IN_HOSPITAL, 'SQUAD_3': SQUAD_3_IN_HOSPITAL}

    def _run(self):
        try:
            # 检查队伍
            self.check_squad()
            # 检查任务
            self.open_mission_board()
            # 修改任务区域
            self.change_mission_sector()

        except NoOpportunityRemain:
            self.close_mission_board()
            logger.warning('The current tribe tower has no remaining opportunities')
        except IsSquadValid:
            logger.warning('The current tribe tower has no remaining opportunities')

    def change_mission_sector(self, skip_first_screenshot=True, index=1):
        logger.info('Change mission sector')

        selected = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(SECTOR_SELECT_CHECK, offset=10) and self.appear_then_click(
                SECTOR_1_NOT_SELECTED, offset=10, interval=1
            ):
                selected = True
                continue

            if self.appear(SECTOR_1_SELECTED, offset=10) and self.appear_then_click(
                SECTOR_SELECT_CONFIRM, offset=10, interval=1
            ):
                continue

            if selected and self.appear(MISSION_1_CONFIG, offset=10):
                break

            if not selected and self.appear_then_click(MISSION_1_CONFIG, offset=10, interval=1):
                continue

    def check_squad(self, skip_first_screenshot=True):
        logger.info('Open squad list')

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(SQUAD_FOLD, offset=10):
                for squad, hospita_check in self.SQUAD_LISTS.items():
                    if not self.appear(globals()[squad], offset=10) or self.appear(hospita_check, offset=10):
                        raise IsSquadValid
                break

            if self.appear_then_click(SQUAD_EXPAND, offset=10, interval=1):
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

            if not self.appear(SURFACE_MISSION_CHECK, offset=10) and self.appear_then_click(
                QUEST, offset=(30, 30), interval=2
            ):
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

    def run(self):
        self.ui_ensure(page_surface)
        self._run()
        # self.config.task_delay(server_update=True)
