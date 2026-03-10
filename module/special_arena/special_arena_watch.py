from module.logger import logger
from module.notify import handle_notify
from module.ocr.ocr import Ocr
from module.special_arena.assets import CURRENT_RANK
from module.special_arena.special_arena import SpecialArena, SpecialArenaIsUnavailable
from module.ui.page import page_arena


class SpecialArenaWatch(SpecialArena):
    def get_current_rank(self):
        model_type = self.config.Optimization_OcrModelType
        ocr_lang = 'ch' if self.config.Client_Language == 'zh-CN' else 'en'

        rank_text = Ocr(
            [CURRENT_RANK],
            lang=ocr_lang,
            model_type=model_type,
            name='CURRENT_RANK',
        ).ocr(self.device.image)['text']

        if rank_text:
            logger.attr(name='CURRENT_RANK', text=rank_text)
            return rank_text
        return ''

    def run(self):
        self.ui_ensure(page_arena)

        try:
            self.ensure_into_special_arena(start_competition=False)
            current_rank = self.get_current_rank()
            previous_rank = (self.config.SpecialArenaWatch_CurrentRank or '')

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
