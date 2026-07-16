import importlib
from functools import cached_property

from module.base.timer import Timer
from module.event.assets import *
from module.logger import logger
from module.ui.assets import GOTO_BACK
from module.ui.ui import UI


class EventSelectError(Exception):
    pass


class EventUnavailableError(Exception):
    pass


class ChallengeNotFoundError(Exception):
    pass


class EventInfo:
    def __init__(self, id, name, type, mini_game, mini_game_play, extend, story_part, story_difficulty,
                 pending_click_offset=(0, 0), pending_finder=None):
        self.id: str = id
        self.name: str = name
        self.type: int = type
        self.mini_game: bool = mini_game
        self.mini_game_play: bool = mini_game_play
        self.extend: bool = extend
        self.story_part: str = story_part
        self.story_difficulty: str = story_difficulty
        self.pending_click_offset = pending_click_offset
        self.pending_finder = pending_finder


class EventBase(UI):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.event_assets = self.load_event_assets('assets')
        if self.event.mini_game:
            self.minigame_assets = self.load_event_assets('assets_game')

    def load_event_assets(self, module_name):
        """动态加载资源模块"""
        event_id = self.event.id
        try:
            module_name = f'module.event.{event_id}.{module_name}'
            return importlib.import_module(module_name)
        except ImportError:
            logger.warning(f'Assets module not found: {module_name}')
            raise EventUnavailableError

    @cached_property
    def event(self) -> EventInfo:
        target_event_id = getattr(self.config, 'EventInfo_Event', None)

        event_config = next(
            (e for e in self.config.EVENTS if e.get('event_id') == target_event_id), self.config.EVENTS[0]
        )
        event_config.setdefault('pending_click_offset', (0, 0))

        for k, v in event_config.items():
            self.config.__setattr__(k, v)

        return EventInfo(
            id=event_config.get('event_id'),
            name=event_config.get('event_name'),
            type=event_config.get('event_type'),
            mini_game=event_config.get('mini_game'),
            mini_game_play=event_config.get('mini_game_play'),
            extend=event_config.get('extend'),
            story_part=event_config.get('story_part'),
            story_difficulty=event_config.get('story_difficulty'),
            pending_click_offset=event_config.get('pending_click_offset', (0, 0)),
            pending_finder=event_config.get('pending_finder'),
        )

    def back_to_event(self):
        logger.info('Back to event')
        click_timer = Timer(0.3)
        event_timer = Timer(3, count=5)

        # 回到活动主页
        while 1:
            self.device.screenshot()

            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                if not event_timer.started():
                    event_timer.start()
                if event_timer.reached():
                    break
            else:
                event_timer.clear()

            if (
                click_timer.reached()
                and not self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(GOTO_BACK, offset=10, interval=2)
            ):
                click_timer.reset()
                continue
