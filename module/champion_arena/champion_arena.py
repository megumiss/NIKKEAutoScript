from functools import cached_property

from module.base.arena import ArenaBase
from module.base.timer import Timer
from module.base.utils import (
    _area_offset,
    point2str,
)
from module.champion_arena.assets import *
from module.logger import logger
from module.ocr.ocr import Digit
from module.ui.assets import ARENA_GOTO_CHAMPION_ARENA, CHAMPION_ARENA_CHECK
from module.ui.page import page_arena
from module.ui.ui import UI


class ChampionArenaIsUnavailable(Exception):
    pass


class ChampionArena(UI, ArenaBase):
    def start_competition(self, skip_first_screenshot=True):
        logger.hr('Start a competition')

        click_timer = Timer(0.3)

        already_start = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if not already_start and click_timer.reached() and self.free_opportunity_remain:
                # 根据策略选择
                opponent_id = 3
                if self.config.OpponentSelection_Enable:
                    opponent_id = self.select_strategy(True)['id']
                opponent = self.button[opponent_id - 1]
                logger.info(f'Secect opponent {opponent_id}')

                self.device.click_minitouch(opponent[0], opponent[1])
                logger.info('Click %s @ %s' % (point2str(opponent[0], opponent[1]), 'START_COMPETITION'))
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(SKIP, offset=(5, 5), interval=1):
                click_timer.reset()
                continue

            if (
                not already_start
                and click_timer.reached()
                and self.appear_then_click(INTO_COMPETITION, offset=(30, 30), interval=5, static=False)
            ):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(END_COMPETITION, offset=5, interval=2):
                logger.info('Click %s @ %s' % (point2str(100, 100), 'END_COMPETITION'))
                self.device.handle_control_check(END_COMPETITION)
                self.device.click_minitouch(100, 100)
                already_start = True
                click_timer.reset()
                continue

            if already_start and self.appear(CHAMPION_ARENA_CHECK, offset=(10, 10), static=False):
                break

        if self.free_opportunity_remain:
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.start_competition()

    def ensure_into_champion_arena(self, skip_first_screenshot=True):
        logger.hr('CHAMPION ARENA START')
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # TODO
            if self.appear(NEXT_SEASON, offset=(50, 50)):
                raise ChampionArenaIsUnavailable

            if click_timer.reached() and self.appear_then_click(ARENA_GOTO_CHAMPION_ARENA, offset=(30, 30), interval=5):
                click_timer.reset()
                continue

            if self.appear(CHAMPION_ARENA_CHECK, offset=(10, 10)):
                break

        if self.free_opportunity_remain:
            self.start_competition()
        else:
            logger.info('There are no free opportunities')

    def run(self):
        self.ui_ensure(page_arena)
        try:
            self.ensure_into_champion_arena()
        except ChampionArenaIsUnavailable:
            pass
        self.config.task_delay(server_update=True)
