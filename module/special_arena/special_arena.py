import time
from functools import cached_property

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
from module.special_arena.assets import *
from module.ui.assets import SPECIAL_ARENA_CHECK, ARENA_GOTO_SPECIAL_ARENA
from module.ui.page import page_arena
from module.ui.ui import UI


class SpecialArenaIsUnavailable(Exception):
    pass


class SpecialArena(UI):
    @property
    def free_opportunity_remain(self) -> bool:
        # 免费票
        result = FREE_OPPORTUNITY_CHECK.appear_on(self.device.image, 20)
        if result:
            logger.info(f"[Free opportunities remain] {result}")
        return result

    @cached_property
    def own_power(self) -> int:
        # 获取战力
        area = _area_offset(OWN_POWER_CHECK.area, (20, -2, 70, 2))
        OWN_POWER = Digit(
            [area],
            name="OWN_POWER",
            letter=(247, 247, 247),
            threshold=128,
            lang="arena",
        )
        return int(OWN_POWER.ocr(self.device.image))

    @cached_property
    def button(self):
        return [(590, 710), (590, 840), (590, 980)]

    def start_competition(self, skip_first_screenshot=True):
        logger.hr("Start a competition")

        confirm_timer = Timer(1, count=5).start()
        click_timer = Timer(0.3)
        click_timer_2 = Timer(5)

        already_start = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                    not already_start
                    and click_timer.reached()
                    and click_timer_2.reached()
                    and self.free_opportunity_remain
            ):
                # TODO 根据战力选择
                self.device.click_minitouch(580, 1080)
                logger.info(
                    "Click %s @ %s"
                    % (point2str(580, 1080), "START_COMPETITION")
                )
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
                    and self.appear_then_click(
                INTO_COMPETITION, offset=(30, 30), interval=5, static=False
            )
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(
                    END_COMPETITION, offset=5, interval=2
            ):
                logger.info("Click %s @ %s" % (point2str(100, 100), "END_COMPETITION"))
                self.device.handle_control_check(END_COMPETITION)
                self.device.click_minitouch(100, 100)
                already_start = True
                confirm_timer.reset()
                click_timer.reset()
                continue

            if (
                    already_start
                    and self.appear(SPECIAL_ARENA_CHECK, offset=(10, 10), static=False)
                    and confirm_timer.reached()
            ):
                break

        if self.free_opportunity_remain:
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.start_competition()

    def ensure_into_special_arena(self, skip_first_screenshot=True):
        confirm_timer = Timer(2, count=3).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(NEXT_SEASON, offset=(50, 50)):
                raise SpecialArenaIsUnavailable

            if click_timer.reached() and self.appear_then_click(
                    ARENA_GOTO_SPECIAL_ARENA, offset=(30, 30), interval=5, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue
            
            if (
                    self.appear(SPECIAL_ARENA_CHECK, offset=(10, 10), static=False)
                    and confirm_timer.reached()
            ):
                break
        
        if self.free_opportunity_remain:
            self.start_competition()
        else:
            logger.info("There are no free opportunities")

    def run(self):
        self.ui_ensure(page_arena)
        try:
            self.ensure_into_special_arena()
        except SpecialArenaIsUnavailable:
            pass
        self.config.task_delay(server_update=True)
