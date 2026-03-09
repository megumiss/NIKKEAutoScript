import re

from module.base.utils import _area_offset
from module.logger import logger
from module.notify import handle_notify
from module.ocr.ocr import Digit, Ocr
from module.special_arena.assets import  OWN_POWER_CHECK
from module.special_arena.special_arena import SpecialArena
from module.ui.page import page_arena


class SpecialArenaIsUnavailable(Exception):
    pass


class SpecialArenaWatch(SpecialArena):
    @property
    def rank_area(self):
        # Current rank is displayed to the left of own power row.
        return _area_offset(OWN_POWER_CHECK.area, (-365, -6, -130, 28))

    @staticmethod
    def _normalize_rank_text(text):
        text = re.sub(r'\s+', '', text or '')
        if not text:
            return ''

        patterns = [
            r'(?:段位|排名|RANK|Rank|TIER|Tier|No\.?|NO\.?|#)[:：]?\s*([A-Za-z0-9一-龥._#-]+)',
            r'#\s*([0-9]+)',
        ]
        for pattern in patterns:
            matched = re.search(pattern, text, flags=re.IGNORECASE)
            if matched:
                return matched.group(1)

        return text[:24]

    def get_current_rank(self):
        model_type = self.config.Optimization_OcrModelType
        ocr_lang = 'ch' if self.config.Client_Language == 'zh-CN' else 'en'

        rank_text = Ocr(
            [self.rank_area],
            lang=ocr_lang,
            model_type=model_type,
            name='SPECIAL_ARENA_CURRENT_RANK',
        ).ocr(self.device.image)['text']
        rank_text = self._normalize_rank_text(rank_text)
        if rank_text:
            logger.attr(name='SPECIAL_ARENA_CURRENT_RANK', text=rank_text)
            return rank_text

        rank_number = Digit(
            [self.rank_area],
            model_type=model_type,
            name='SPECIAL_ARENA_CURRENT_RANK_NUM',
        ).ocr(self.device.image)['text']
        if rank_number and rank_number != '0':
            logger.attr(name='SPECIAL_ARENA_CURRENT_RANK', text=rank_number)
            return rank_number

        return ''

    def run(self):
        self.ui_ensure(page_arena)

        try:
            self.ensure_into_special_arena()
            current_rank = self.get_current_rank()
            previous_rank = (self.config.SpecialArenaWatch_CurrentRank or '').strip()

            if not current_rank:
                logger.warning('Current rank not detected, skip rank change check')
            elif current_rank != previous_rank:
                logger.info(f'Special Arena rank changed: {previous_rank or "-"} -> {current_rank}')
                handle_notify(
                    config=self.config,
                    title_key='SpecialArenaRankChanged.title',
                    content_key='SpecialArenaRankChanged.content',
                    old_rank=previous_rank or '-',
                    new_rank=current_rank,
                    always=self.config.Notification_WinOnePush,
                )
                self.config.SpecialArenaWatch_CurrentRank = current_rank
            else:
                logger.info(f'Special Arena rank unchanged: {current_rank}')
        except SpecialArenaIsUnavailable:
            logger.warning('Waiting for the next season')
        finally:
            interval = self.config.SpecialArenaWatch_CheckInterval or 10
            self.config.task_delay(minute=interval)
