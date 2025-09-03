import random
from functools import cached_property

from module.base.arena import ArenaBase
from module.base.timer import Timer
from module.champion_arena.assets import *
from module.logger import logger
from module.ocr.ocr import Digit
from module.ui.assets import ARENA_GOTO_CHAMPION_ARENA, CHAMPION_ARENA_CHECK
from module.ui.page import page_arena
from module.ui.ui import UI


class ChampionArenaIsUnavailable(Exception):
    pass


class ChampionArena(UI, ArenaBase):
    @cached_property
    def cheers(self):
        return [CHEER_SELECT_1, CHEER_SELECT_2]

    @cached_property
    def cheer_count(self) -> int:
        """获取左侧应援数值"""
        model_type = self.config.Optimization_OcrModelType
        CHEER_LEFT_COUNT = Digit(
            [CHEER_COUNT.area],
            name='CHEER_LEFT_COUNT',
            model_type=model_type,
            lang='num',
        )
        return int(CHEER_LEFT_COUNT.ocr(self.device.image)['text'])

    @property
    def cheer_select(self) -> int:
        """根据策略确定应援选择"""

        if self.config.CheerStrategy_Strategy == 'Most':
            return 0 if self.cheer_count > 50 else 1
        elif self.config.CheerStrategy_Strategy == 'Few':
            return 1 if self.cheer_count > 50 else 0
        elif self.config.CheerStrategy_Strategy == 'Random':
            return random.randint(0, 1)
        else:
            return 0

    def cheer(self, skip_first_screenshot=True):
        """应援"""
        logger.info('Open promotion or champion')
        click_timer = Timer(0.3)

        # 打开晋级赛或者冠军争霸赛
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 晋级赛
            if click_timer.reached() and self.appear_then_click(PROMOTION, offset=10, interval=3):
                click_timer.reset()
                continue

            # 冠军争霸赛
            if click_timer.reached() and self.appear_then_click(CHAMPION, offset=10, interval=3):
                click_timer.reset()
                continue

            # 应援结果
            if (
                click_timer.reached()
                and self.appear(CHEER_RESULT, offset=10, static=False)
                and self.appear_then_click(CHEER_RESULT_NEXT, offset=10, interval=1, static=False)
            ):
                click_timer.reset()
                continue

            # 检查应援按钮
            if self.appear(PROMOTION_CHECK, offset=30) or self.appear(CHAMPION_CHECK, offset=30):
                if self.appear(CHEER_ENABLE, offset=10):
                    click_timer.reset()
                    break
                else:
                    logger.info('Cheer already done')
                    raise ChampionArenaIsUnavailable

        logger.info('Cheer a nikke')
        cheer_done = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and (self.appear(PROMOTION_CHECK, offset=30) or self.appear(CHAMPION_CHECK, offset=30))
                and self.appear_then_click(CHEER_ENABLE, offset=10, interval=2)
            ):
                click_timer.reset()
                continue

            # 应援选择
            if (
                click_timer.reached()
                and self.appear(CHEER_CHECK, offset=10)
                and self.appear_then_click(self.cheers[self.cheer_select], threshold=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 应援确认
            if (
                click_timer.reached()
                and self.appear(CHEER_CHECK, offset=10)
                and (not self.appear(self.cheers[0], threshold=10) or not self.appear(self.cheers[1], threshold=10))
                and self.appear_then_click(CHEER_CONFIRM, threshold=10, interval=1)
            ):
                cheer_done = True
                click_timer.reset()
                continue

            # 关闭
            if (
                cheer_done
                and self.appear(CHEER_CHECK, offset=10)
                and (not self.appear(self.cheers[0], threshold=10) or not self.appear(self.cheers[1], threshold=10))
                and self.appear_then_click(CHEER_CANCEL, threshold=10, interval=1)
            ):
                logger.info('Cheer done')
                break

    def ensure_into_champion_arena(self, skip_first_screenshot=True):
        logger.hr('CHAMPION ARENA START')
        confirm_timer = Timer(3, count=3)
        click_timer = Timer(0.3)

        # 进入竞技场
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                self.appear(PROGRESS_NEXT_SEASON, offset=(30, 30))
                or self.appear(PROGRESS_TEAM, offset=(30, 30))
                or self.appear(PROGRESS_FIGHTING, offset=(30, 30))
                or self.appear(PROGRESS_RESULTING, offset=(30, 30))
            ):
                logger.warning('Champion arena in preparation')
                raise ChampionArenaIsUnavailable

            if click_timer.reached() and self.appear_then_click(ARENA_GOTO_CHAMPION_ARENA, offset=30, interval=3):
                click_timer.reset()
                continue

            # 应援结果
            if (
                click_timer.reached()
                and self.appear(CHEER_RESULT, offset=10, static=False)
                and self.appear_then_click(CHEER_RESULT_NEXT, offset=10, interval=1, static=False)
            ):
                click_timer.reset()
                continue

            if self.appear(CHAMPION_ARENA_CHECK, offset=30):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    break
            else:
                confirm_timer.clear()

    def run(self):
        self.ui_ensure(page_arena)
        try:
            self.ensure_into_champion_arena()
            self.cheer()
        except ChampionArenaIsUnavailable:
            pass
        self.config.task_delay(server_update=True)
