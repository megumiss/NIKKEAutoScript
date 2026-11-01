"""Resolve the current event chapter independently of template maintenance."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from module.logger import logger


STORY_STAGES = {
    'story_1_normal': ('Story_1', 'Normal'),
    'story_1_hard': ('Story_1', 'Hard'),
    'story_2_normal': ('Story_2', 'Normal'),
    'story_2_hard': ('Story_2', 'Hard'),
}
STAGE_INTERVAL = timedelta(days=7)


@dataclass(frozen=True)
class EventStage:
    story_part: str
    story_difficulty: str
    next_change_at: Optional[datetime] = None


def available_story_stages(event):
    if event.get('event_type') == 2:
        return ('story_1_normal', 'story_1_hard')
    return ('story_1_normal', 'story_2_normal', 'story_2_hard')


def resolve_event_stage(event, now=None, manual=None):
    """UI manual gate > seven-day schedule > legacy current-stage fields.

    Schedule timestamps must include a timezone. The final stage has no
    automatic expiry; activity availability is still checked in the game.
    """
    fallback = EventStage(event.get('story_part'), event.get('story_difficulty'))
    manual = manual or {}
    use_manual = manual.get('ManualStage') is True and manual.get('Event') == event.get('event_id')
    schedule = event.get('story_schedule')
    if not use_manual and schedule is None:
        return fallback

    try:
        if use_manual:
            selected = (manual.get('StoryPart'), manual.get('StoryDifficulty'))
            if selected not in [STORY_STAGES[stage] for stage in available_story_stages(event)]:
                raise ValueError(f'unsupported manual stage: {selected!r}')
            return EventStage(*selected)

        if not isinstance(schedule, dict):
            raise ValueError('story_schedule must be an object')
        start = schedule.get('start_at')
        if not isinstance(start, str):
            raise ValueError('story_schedule.start_at must be an ISO 8601 timestamp')
        start = datetime.fromisoformat(start.replace('Z', '+00:00'))
        if start.utcoffset() is None:
            raise ValueError('story_schedule.start_at must include a timezone')
        stages = schedule.get('stages')
        if not isinstance(stages, (list, tuple)) or not stages:
            raise ValueError('story_schedule.stages must be a non-empty list')
        if any(not isinstance(stage, str) or stage not in STORY_STAGES for stage in stages):
            raise ValueError('story_schedule.stages contains an unknown stage')
        if len(set(stages)) != len(stages):
            raise ValueError('story_schedule.stages must not contain duplicates')
        if any(stage not in available_story_stages(event) for stage in stages):
            raise ValueError('story_schedule contains stages unsupported by this event type')

        now = now if now is not None else datetime.now(timezone.utc)
        if now.utcoffset() is None:
            raise ValueError('current time must include a timezone')
        # UTC arithmetic keeps each interval exactly seven days across DST changes.
        start = start.astimezone(timezone.utc)
        now = now.astimezone(timezone.utc)
        if now < start:
            return EventStage(fallback.story_part, fallback.story_difficulty, start)
        index = min((now - start) // STAGE_INTERVAL, len(stages) - 1)
        next_change = start + STAGE_INTERVAL * (index + 1) if index + 1 < len(stages) else None
        return EventStage(*STORY_STAGES[stages[index]], next_change)
    except (ValueError, OverflowError) as exc:
        logger.warning(f'Invalid stage configuration for {event.get("event_id")}: {exc}; use legacy stage')
        return fallback
