"""Activity calendar data for the SPA dashboard."""

import asyncio
import hashlib
import json
import time
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.deep import deep_get
from module.config.utils import nkas_instance
from module.logger import logger
from module.webui.setting import State


CALENDAR_URL = 'https://api.blablalink.com/api/ugc/direct/standalonesite/Dynamics/GetCalendarDetail'
CALENDAR_CACHE_TTL = 60 * 60
NIKKE_RESOURCE_HOST = 'https://sg-tools-cdn.blablalink.com'
PASS_BACKGROUND_URL = (
    'https://www.blablalink.com/assets/nikke/version/default/assets/eventpass-bg-BErS4EuI.png'
)
STATIC_BANNER_URLS = {
    'ArenaRookieSeason': '/static/gui/calendar/arena/rookie.png',
    'ArenaSpecialSeason': '/static/gui/calendar/arena/special.png',
    'ArenaChampionSeason': '/static/gui/calendar/arena/champion.png',
    'SimulationOverclockSeason': (
        'https://www.blablalink.com/assets/nikke/version/default/assets/simulation_room-CZhRdwf-.png'
    ),
    'SoloRaid': 'https://www.blablalink.com/assets/nikke/version/default/assets/raid1-Bf8sLNRY.png',
    'UnionRaid': 'https://www.blablalink.com/assets/nikke/version/default/assets/raid2-BfIpEslI.png',
    'CooperationEvent': 'https://www.blablalink.com/assets/nikke/version/default/assets/raid3-Dpo64P5E.png',
}
UI_LANGUAGE_MAP = {
    'zh-CN': 'zh-TW',
    'en-US': 'en',
    'ja-JP': 'ja',
}
FALLBACK_COMMON_PARAMS = {
    'game_id': '16',
    'area_id': 'global',
    'source': 'pc_web',
    'intl_game_id': '29080',
    'env': 'prod',
    'data_statistics_scene': 'outer',
    'data_statistics_page_id': 'https://www.blablalink.com/activity-calendar',
    'data_statistics_client_type': 'pc_web',
}
LARGE_PRIMES = (224737, 1000639, 2654435761, 2654435769, 1000621, 4294967291)
ARENA_TYPES = {'ArenaRookieSeason', 'ArenaSpecialSeason', 'ArenaChampionSeason'}
CATEGORY_ORDER = {
    'character_gacha': 0,
    'raid': 1,
    'simulation_room': 2,
    'skin_gacha': 3,
    'version_event': 4,
    'arena': 5,
}

BASE_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en',
    'content-type': 'application/json',
    'origin': 'https://www.blablalink.com',
    'referer': 'https://www.blablalink.com/activity-calendar',
    'x-channel-type': '2',
}

_cache_payload: Optional[Tuple[Dict[str, Any], float, int, str]] = None
_cache_lock = asyncio.Lock()


class CalendarRequestError(Exception):
    pass


def _int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def _djb2(value: str, seed: int) -> int:
    result = seed
    for character in value:
        result = _int32(result * 33 + ord(character))
    return result


def _path_token(value: str, prime: int) -> str:
    hashed = _djb2(value, prime) % prime
    letters = chr(97 + (hashed // 26) % 26) + chr(97 + hashed % 26)
    return f'{letters}-{hashed % 99:02d}'


def nikke_resource_url(logical_path: str) -> str:
    """Build the obfuscated game-resource URL used by the official calendar."""
    source = logical_path.lstrip('/')
    parts = [part for part in source.split('/') if part]
    if not parts or len(parts) > len(LARGE_PRIMES) + 1:
        return ''
    extension = '.'.join(parts[-1].split('.')[1:])
    if not extension:
        return ''
    directories = [_path_token(source, LARGE_PRIMES[index]) for index in range(len(parts) - 1)]
    filename = f'{hashlib.md5(source.encode("utf-8")).hexdigest()}.{extension}'
    return f'{NIKKE_RESOURCE_HOST}/{"/".join(directories + [filename])}'


def _calendar_language(language: Any) -> str:
    value = str(language or '').strip()
    if value in {'zh-TW', 'en', 'ja'}:
        return value
    if value in UI_LANGUAGE_MAP:
        return UI_LANGUAGE_MAP[value]
    lowered = value.lower()
    if lowered.startswith('zh'):
        return 'zh-TW'
    if lowered.startswith('ja'):
        return 'ja'
    return 'en'


def _common_params(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    value = deep_get(config, 'BlaAuth.BlaAuth.XCommonParams')
    if not value:
        return None
    try:
        data = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _request_headers(ui_language: str) -> Tuple[Dict[str, str], str]:
    headers = BASE_HEADERS.copy()
    configs = []
    for name in nkas_instance():
        try:
            config = State.config_updater.read_file(name)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(f'Unable to read BlaAuth settings for calendar from {name}: {exc}')
            continue
        configs.append(config)

    auth_config = next((config for config in configs if deep_get(config, 'BlaAuth.BlaAuth.Cookie')), None)
    if auth_config is None:
        auth_config = next((config for config in configs if _common_params(config)), None)

    common_data = None
    if auth_config is not None:
        cookie = deep_get(auth_config, 'BlaAuth.BlaAuth.Cookie')
        user_agent = deep_get(auth_config, 'BlaAuth.BlaAuth.UserAgent')
        common_data = _common_params(auth_config)
        if cookie:
            headers['cookie'] = str(cookie)
        if user_agent:
            headers['user-agent'] = str(user_agent)

    if common_data is None:
        for config in configs:
            candidate_data = _common_params(config)
            if candidate_data:
                common_data = candidate_data
                break
    effective_language = _calendar_language(ui_language)
    request_common_params = dict(common_data or FALLBACK_COMMON_PARAMS)
    request_common_params['language'] = effective_language
    request_common_params['data_statistics_lang'] = effective_language
    headers['x-common-params'] = json.dumps(request_common_params, ensure_ascii=False, separators=(',', ':'))
    headers['x-language'] = effective_language
    return headers, effective_language


async def _fetch_calendar(headers: Dict[str, str]) -> Dict[str, Any]:
    timeout = httpx.Timeout(15.0, connect=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(CALENDAR_URL, headers=headers, json={})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CalendarRequestError(f'Unable to load activity calendar: {exc}') from exc
    if not isinstance(payload, dict) or payload.get('code') != 0:
        message = payload.get('msg', 'Unknown response') if isinstance(payload, dict) else 'Invalid response'
        raise CalendarRequestError(f'Activity calendar request failed: {message}')
    if not isinstance(payload.get('data'), dict):
        raise CalendarRequestError('Activity calendar response does not contain data.')
    return payload


async def _get_calendar_payload(
    force_refresh: bool,
    ui_language: str,
) -> Tuple[Dict[str, Any], bool, int, str]:
    global _cache_payload

    headers, effective_language = _request_headers(ui_language)
    now = time.monotonic()
    cached = _cache_payload
    if (
        not force_refresh
        and cached is not None
        and cached[3] == effective_language
        and now - cached[1] < CALENDAR_CACHE_TTL
    ):
        return cached[0], True, cached[2], cached[3]
    async with _cache_lock:
        now = time.monotonic()
        cached = _cache_payload
        if (
            not force_refresh
            and cached is not None
            and cached[3] == effective_language
            and now - cached[1] < CALENDAR_CACHE_TTL
        ):
            return cached[0], True, cached[2], cached[3]
        payload = await _fetch_calendar(headers)
        updated_at = int(time.time())
        _cache_payload = (payload, time.monotonic(), updated_at, effective_language)
        return payload, False, updated_at, effective_language


def _module_items(data: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any], int]]:
    source_index = 0
    for category in CATEGORY_ORDER:
        module = data.get(category, {})
        items = module.get('items', []) if isinstance(module, dict) else []
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                yield category, item, source_index
                source_index += 1


def _is_allowed(category: str, item: Dict[str, Any]) -> bool:
    event_type = str(item.get('type', ''))
    if category == 'simulation_room':
        return event_type == 'SimulationOverclockSeason'
    if category == 'version_event':
        return event_type in {'StoryEvent', 'FieldHubEvent'}
    if category == 'arena':
        return str(item.get('arena_type') or event_type) in ARENA_TYPES
    return category in CATEGORY_ORDER


def _event_title(category: str, item: Dict[str, Any]) -> Tuple[str, str, Optional[str]]:
    name = str(item.get('name', '')).strip()
    description = str(item.get('description', '')).strip()
    if category == 'character_gacha':
        characters = item.get('characters', [])
        names = [str(character.get('name', '')).strip() for character in characters if isinstance(character, dict)]
        title = ' / '.join(value for value in names if value) or name
        return title, name if title != name else description, None
    if category == 'skin_gacha':
        event_type = str(item.get('type', ''))
        subtype = None
        if event_type == 'BoxGachaEvent':
            subtype = 'roulette'
        elif event_type in {'EventPass', 'SeasonPass'}:
            subtype = 'pass'
        title = str(item.get('costume_name') or item.get('theme_name') or name).strip()
        subtitle = str(item.get('theme_name') or name).strip()
        return title, subtitle if subtitle != title else description, subtype
    return name, description, None


def _banner_data(item: Dict[str, Any]) -> Dict[str, Any]:
    banner = str(item.get('banner', '')).strip()
    event_type = str(item.get('type', ''))
    if not banner:
        static_banner = STATIC_BANNER_URLS.get(event_type, '')
        if static_banner:
            return {'banner_mode': 'image', 'banner_url': static_banner}
        return {'banner_mode': 'placeholder', 'banner_url': ''}
    if event_type != 'SeasonPass':
        return {
            'banner_mode': 'image',
            'banner_url': nikke_resource_url(f'schedule/banner/{banner}.webp'),
        }
    resource_id = str(item.get('resource_id', '')).strip().zfill(3)
    try:
        costume_index = int(item.get('costume_index', 0))
    except (TypeError, ValueError):
        costume_index = 0
    return {
        'banner_mode': 'pass_composite',
        'banner_url': nikke_resource_url(f'icon/Logo/pass/{banner}.webp'),
        'character_url': nikke_resource_url(
            f'character/full/c{resource_id}_{costume_index:02d}.webp'
        ) if resource_id.strip('0') else '',
        'background_url': PASS_BACKGROUND_URL,
    }


def _current_overclock_stage(data: Dict[str, Any], current_time: int) -> Optional[Tuple[int, int]]:
    module = data.get('simulation_room', {})
    items = module.get('items', []) if isinstance(module, dict) else []
    for item in items:
        if not isinstance(item, dict) or not str(item.get('type', '')).startswith('SimulationOverclockSeason_Sub_'):
            continue
        try:
            start_time = int(item['start_time'])
            end_time = int(item['end_time'])
        except (KeyError, TypeError, ValueError):
            continue
        if start_time <= current_time <= end_time:
            return start_time, end_time
    return None


def _normalise_events(payload: Dict[str, Any], current_time: int) -> list[Dict[str, Any]]:
    events = []
    data = payload.get('data', {})
    overclock_stage = _current_overclock_stage(data, current_time)
    for category, item, source_index in _module_items(data):
        if not _is_allowed(category, item):
            continue
        try:
            start_time = int(item['start_time'])
            end_time = int(item['end_time'])
        except (KeyError, TypeError, ValueError):
            continue
        if not start_time <= current_time <= end_time:
            continue
        event_type = str(item.get('type', ''))
        title, subtitle, subtype = _event_title(category, item)
        identifier = item.get('banner') or item.get('resource_id') or source_index
        event = {
            'id': f'{category}:{event_type}:{start_time}:{identifier}',
            'category': category,
            'type': event_type,
            'subtype': subtype,
            'title': title,
            'subtitle': subtitle,
            'start_time': start_time,
            'end_time': end_time,
            'source_order': source_index,
            **_banner_data(item),
        }
        if category == 'simulation_room' and overclock_stage:
            event['stage_start_time'] = overclock_stage[0]
            event['stage_end_time'] = overclock_stage[1]
        events.append(event)
    events.sort(key=lambda event: (
        event['end_time'],
        CATEGORY_ORDER[event['category']],
        event['start_time'],
        event['source_order'],
    ))
    return events


async def calendar(request: Request):
    force_refresh = request.query_params.get('refresh', '').lower() in {'1', 'true', 'yes'}
    ui_language = request.query_params.get('language', 'zh-CN')
    try:
        payload, cached, updated_at, language = await _get_calendar_payload(force_refresh, ui_language)
    except CalendarRequestError as exc:
        logger.warning(str(exc))
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=502)
    events = _normalise_events(payload, int(time.time()))
    return JSONResponse({
        'updated_at': updated_at,
        'cached': cached,
        'language': language,
        'items': events,
    })
