import time
from functools import cached_property

from module.base.arena import ArenaBase
from module.base.timer import Timer
from module.base.utils import (
    _area_offset,
    crop,
    extract_letters,
    find_letter_area,
    float2str,
    point2str,
)
from module.logger import logger
from module.ocr.ocr import Digit
from module.rookie_arena.assets import *
from module.ui.assets import ARENA_GOTO_ROOKIE_ARENA, ROOKIE_ARENA_CHECK
from module.ui.page import page_arena
from module.ui.ui import UI


class RookieArenaIsUnavailable(Exception):
    pass


class RookieArena(UI, ArenaBase):
    @cached_property
    def button(self):
        return [(590, 730), (590, 900), (590, 1100)]

    @cached_property
    def coordinate_config(self) -> list[dict]:
        """
        返回战力、等级识别区域
        """
        return [
            {
                'Power': (395, 650, 470, 675),
                'Ranking': (85, 765, 120, 790),
                'CommanderLevel': (74, 733, 116, 750),
                'SynchroLevel': (308, 779, 329, 797),
            },
            {
                'Power': (395, 830, 470, 855),
                'Ranking': (85, 945, 120, 970),
                'CommanderLevel': (74, 911, 116, 928),
                'SynchroLevel': (308, 957, 329, 976),
            },
            {
                'Power': (395, 1010, 470, 1035),
                'Ranking': (85, 1125, 120, 1150),
                'CommanderLevel': (74, 1089, 116, 1106),
                'SynchroLevel': (308, 1137, 329, 1155),
            },
        ]

    FIELD_LETTERS = {
        'Power': (107, 107, 107),
        'Ranking': (107, 107, 107),
        'CommanderLevel': (222, 222, 222),
        'SynchroLevel': (255, 255, 255),
    }

    @property
    def free_opportunity_remain(self) -> bool:
        result = FREE_OPPORTUNITY_CHECK.appear_on(self.device.image, 20)
        if result:
            logger.info(f'[Free opportunities remain] {result}')
        return result

    @property
    def competitor_power_list(self) -> list[int]:
        start_time = time.time()
        r = [i.get('area') for i in POWER_CHECK.match_several(self.device.image, threshold=0.66, static=False)]
        # 按照 upper 排序
        r.sort(key=lambda x: x[1])
        r = [_area_offset(i, (22, -10, 65, 8)) for i in r]

        r = [
            self.ocr_models.__getattribute__('arena').ocr(
                crop(
                    crop(self.device.image, i),
                    _area_offset(
                        find_letter_area(extract_letters(crop(self.device.image, i), letter=(90, 93, 99)) < 128),
                        (-2, -2, 3, 2),
                    ),
                )
            )
            for i in r
        ]

        r = list(map(lambda x: int(x[0]['text']), r))
        logger.attr(
            name='%s %ss' % ('COMPETITOR_POWER_LIST', float2str(time.time() - start_time)),
            text=str(r),
        )

        return r

    @cached_property
    def own_power(self) -> int:
        area = _area_offset(OWN_POWER_CHECK.area, (20, -2, 70, 2))
        model_type = self.config.Optimization_OcrModelType
        OWN_POWER = Digit(
            [area],
            name='OWN_POWER',
            model_type=model_type,
            lang='num',
        )
        return int(OWN_POWER.ocr(self.device.image)['text'])

    def start_competition(self, skip_first_screenshot=True):
        logger.hr('Start a competition')

        confirm_timer = Timer(1, count=5).start()
        click_timer = Timer(0.3)
        click_timer_2 = Timer(5)

        already_start = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if not already_start and click_timer.reached() and click_timer_2.reached() and self.free_opportunity_remain:
                # 根据策略选择
                opponent_id = 3
                if self.config.OpponentSelection_Enable:
                    opponent_id = self.select_strategy(False)['id']
                opponent = self.button[opponent_id - 1]
                logger.info(f'Secect opponent {opponent_id}')

                self.device.click_minitouch(opponent[0], opponent[1])
                logger.info('Click %s @ %s' % (point2str(opponent[0], opponent[1]), 'START_COMPETITION'))
                confirm_timer.reset()
                click_timer.reset()
                click_timer_2.reset()
                continue

            if click_timer.reached() and self.appear_then_click(SKIP, offset=(5, 5), interval=1):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if (
                not already_start
                and click_timer.reached()
                and self.appear_then_click(INTO_COMPETITION, offset=(30, 30), interval=5, static=False)
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(END_COMPETITION, offset=5, interval=2):
                logger.info('Click %s @ %s' % (point2str(100, 100), 'END_COMPETITION'))
                self.device.handle_control_check(END_COMPETITION)
                self.device.click_minitouch(100, 100)
                already_start = True
                confirm_timer.reset()
                click_timer.reset()
                continue

            if already_start and self.appear(ROOKIE_ARENA_CHECK, offset=(10, 10)) and confirm_timer.reached():
                break

        if self.free_opportunity_remain:
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.start_competition()

    def ensure_into_rookie_arena(self, skip_first_screenshot=True):
        confirm_timer = Timer(2, count=3).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(NEXT_SEASON, offset=(50, 50)):
                raise RookieArenaIsUnavailable

            if click_timer.reached() and self.appear_then_click(
                ARENA_GOTO_ROOKIE_ARENA, offset=(30, 30), interval=5, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if self.appear(ROOKIE_ARENA_CHECK, offset=(10, 10), static=False) and confirm_timer.reached():
                break

        if self.free_opportunity_remain:
            self.start_competition()
        else:
            logger.info('There are no free opportunities')

    def run(self):
        self.ui_ensure(page_arena)
        try:
            self.ensure_into_rookie_arena()
        except RookieArenaIsUnavailable:
            pass
        self.config.task_delay(server_update=True)
