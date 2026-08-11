"""Maintenance notice for the SPA: fetched from the official BlaBlaLink notice plate.

Design (agreed with the team):
- The official notice plate (plate 43 / 'notice2') is the source.  A notice is
  cached on disk (id + parsed period + official text + language).
- While the cached period is still valid, the official endpoints are hit at
  most once per script start: the first request after launch runs a list
  fetch and overwrites the cache when a new/changed notice exists; afterwards
  the cache is served as-is until the period expires (then the list is used
  again to discover the next notice).
- A failed request degrades to the last cached notice instead of hiding the
  banner.  Times are stored as UTC epochs; the SPA renders them locally.
"""

import asyncio
import calendar
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from module.logger import logger
from .routes_calendar import _request_headers


POST_LIST_URL = 'https://api.blablalink.com/api/ugc/direct/standalonesite/Dynamics/GetPostList'
POST_DETAIL_URL = 'https://api.blablalink.com/api/ugc/direct/standalonesite/Dynamics/GetPost'
NOTICE_PLATE_ID = 43
NOTICE_PLATE_UNIQUE_ID = 'notice2'
NOTICE_LIST_LIMIT = '20'
CACHE_FILE = Path('./log/maintenance_cache.json')
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
# Module load time equals the script start time; a cached `fetched_at` earlier
# than this marks the first request of this process, the only one allowed to
# hit the official endpoints while the cached period is still valid.
PROCESS_START_TIME = time.time()

# Match a maintenance period.  The official notices differ by language:
#   en:  "Maintenance Period 8/13 00:00 ~ 07:00 (UTC+9)"      (same day)
#   zh:  "維護時間：2026年8月12日 23:00 ～ 2026年8月13日 06:00 (UTC+8)"  (cross-day, UTC+8 HK/TW server)
# so two patterns are tried in turn; both tolerate an explicit year.
_TZ_TOKEN = r'UTC(?:\s*[+-]\s*\d{1,2})?|JST|KST|GMT'
_CN_PATTERN = re.compile(
    r'(?:(?P<sy>\d{4})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})日\s+'
    r'(?P<sh>\d{1,2}):(?P<sm>\d{2})\s*[~～\-–—]\s*'
    r'(?:(?:(?P<ey>\d{4})年)?(?P<em>\d{1,2})月(?P<ed>\d{1,2})日\s+)?'
    r'(?P<eh>\d{1,2}):(?P<mn>\d{2})\s*'
    r'(?:\(?\s*(?P<tz>' + _TZ_TOKEN + r')\s*\)?)?',
)
_SLASH_PATTERN = re.compile(
    r'(?:(?P<sy>\d{4})[/-])?(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+'
    r'(?P<sh>\d{1,2}):(?P<sm>\d{2})\s*[~～\-–—]\s*'
    r'(?:(?:(?P<ey>\d{4})[/-])?(?P<em>\d{1,2})/(?P<ed>\d{1,2})\s+)?'
    r'(?P<eh>\d{1,2}):(?P<mn>\d{2})\s*'
    r'(?:\(?\s*(?P<tz>' + _TZ_TOKEN + r')\s*\)?)?',
)
MAINTENANCE_KEYWORDS = ('maintenance', '维护', '維護', 'メンテナンス')

_cache_lock = asyncio.Lock()


def _strip_html(value: Any) -> str:
    text = re.sub(r'<[^>]+>', ' ', str(value or ''))
    return re.sub(r'\s+', ' ', text).strip()


def _offset_hours(tz: Optional[str]) -> int:
    if not tz:
        # Official notices default to UTC+9 (JST/KST); keep that assumption
        # only when no timezone token is present at all.
        return 9
    token = tz.upper().replace('＋', '+').replace(' ', '')
    if token in {'UTC', 'GMT'}:
        return 0
    if token in {'JST', 'KST'}:
        return 9
    match = re.search(r'[+-]\d{1,2}', token)
    return int(match.group(0)) if match else 0


def _nearest_future(year: int, month: int, day: int, hour: int, minute: int, offset: int) -> int:
    """Resolve a year-less wall-clock time to the closest epoch in the future.

    Official notices are announced days ahead; if this year's date already
    passed (or is far ahead of the announcement) fall back to next year.
    """
    now = time.time()
    candidates = []
    for candidate_year in (year, year + 1):
        try:
            naive = calendar.timegm((candidate_year, month, day, hour, minute, 0))
        except (OverflowError, ValueError):
            continue
        candidates.append(naive - offset * 3600)
    if not candidates:
        return 0
    future = [value for value in candidates if value > now]
    if future:
        return min(future)
    return max(candidates)


def parse_period(content: Any) -> Optional[Dict[str, Any]]:
    """Parse a maintenance period from post content.

    Returns {'start_time', 'end_time', 'official_period'} or None.
    """
    text = _strip_html(content)
    match = None
    for pattern in (_CN_PATTERN, _SLASH_PATTERN):
        match = pattern.search(text)
        if match:
            break
    if not match:
        return None
    month = int(match.group('month'))
    day = int(match.group('day'))
    start_hour = int(match.group('sh'))
    start_minute = int(match.group('sm'))
    end_hour = int(match.group('eh'))
    end_minute = int(match.group('mn'))
    offset = _offset_hours(match.group('tz'))

    explicit_year = match.group('sy')
    if explicit_year:
        try:
            start_time = calendar.timegm((int(explicit_year), month, day, start_hour, start_minute, 0))
        except (OverflowError, ValueError):
            return None
        start_time -= offset * 3600
    else:
        current_year = time.localtime().tm_year
        start_time = _nearest_future(current_year, month, day, start_hour, start_minute, offset)
        if not start_time:
            return None

    end_month = match.group('em')
    if end_month:
        end_month = int(end_month)
        end_day = int(match.group('ed'))
        end_year = match.group('ey')
        if end_year:
            end_time = calendar.timegm((int(end_year), end_month, end_day, end_hour, end_minute, 0))
        else:
            start_year = time.gmtime(start_time).tm_year
            end_time = calendar.timegm((start_year, end_month, end_day, end_hour, end_minute, 0))
            if end_time <= start_time:
                end_time = calendar.timegm((start_year + 1, end_month, end_day, end_hour, end_minute, 0))
        end_time -= offset * 3600
    else:
        # Same-day period: the end wall-clock belongs to the same day, guard a
        # cross-midnight wrap just in case.
        duration = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
        if duration <= 0:
            duration += 24 * 60
        end_time = start_time + duration * 60

    official_period = match.group(0).strip()
    return {'start_time': start_time, 'end_time': end_time, 'official_period': official_period}


def _is_maintenance_post(post: Dict[str, Any]) -> bool:
    if post.get('is_official') != 1:
        return False
    haystack = f"{post.get('title', '')} {post.get('content_summary', '')}".lower()
    return any(keyword in haystack for keyword in MAINTENANCE_KEYWORDS)


def _post_meta(post: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a post (list item or detail) into a cache entry skeleton."""
    content = post.get('content') or post.get('content_summary')
    parsed = parse_period(content)
    if not parsed:
        return {}
    modified = int(post.get('modified_on') or post.get('created_on') or 0)
    return {
        'post_uuid': str(post.get('post_uuid') or ''),
        'title': str(post.get('title') or ''),
        'modified_on': modified,
        **parsed,
    }


def _load_cache() -> Optional[Dict[str, Any]]:
    if not CACHE_FILE.is_file():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) and data.get('post_uuid') else None
    except (OSError, ValueError):
        logger.warning('Unable to read maintenance cache, ignored')
        return None


def _save_cache(entry: Dict[str, Any]) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError as exc:
        logger.warning(f'Unable to write maintenance cache: {exc}')


def _clear_cache() -> None:
    try:
        CACHE_FILE.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f'Unable to remove maintenance cache: {exc}')


async def _post_json(url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f'Maintenance request failed ({url}): {exc}')
        return None
    if not isinstance(payload, dict) or payload.get('code') != 0:
        logger.warning(f'Maintenance request rejected ({url}): {payload.get("msg", "unknown") if isinstance(payload, dict) else payload}')
        return None
    return payload


async def _fetch_post_list(headers: Dict[str, str]) -> List[Dict[str, Any]]:
    payload = await _post_json(POST_LIST_URL, headers, {
        'search_type': 0,
        'plate_id': NOTICE_PLATE_ID,
        'plate_unique_id': NOTICE_PLATE_UNIQUE_ID,
        'nextPageCursor': '',
        'order_by': 1,
        'limit': NOTICE_LIST_LIMIT,
    })
    data = payload.get('data', {}) if payload else {}
    posts = data.get('list', []) if isinstance(data, dict) else []
    return [post for post in posts if isinstance(post, dict)]


async def _fetch_post_detail(headers: Dict[str, str], post_uuid: str) -> Optional[Dict[str, Any]]:
    payload = await _post_json(POST_DETAIL_URL, headers, {'post_uuid': post_uuid})
    data = payload.get('data', {}) if payload else None
    return data if isinstance(data, dict) else None


async def _discover(headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Fetch the official list, parse every maintenance post, keep the best one."""
    posts = await _fetch_post_list(headers)
    candidates = [_post_meta(post) for post in posts if _is_maintenance_post(post)]
    candidates = [entry for entry in candidates if entry]
    if not candidates:
        return None
    for entry in candidates:
        detail = await _fetch_post_detail(headers, entry['post_uuid'])
        if not detail:
            continue
        refreshed = _post_meta(detail)
        if refreshed:
            # The detail response does not carry modified_on (always 0), keep
            # the list-level value for the cache record.
            refreshed['modified_on'] = entry['modified_on']
            entry.update(refreshed)
    now = time.time()
    upcoming = [entry for entry in candidates if entry['end_time'] > now]
    pool = upcoming or candidates
    return max(pool, key=lambda entry: entry['start_time'])


def _response(entry: Optional[Dict[str, Any]], source: str, error: str = '') -> JSONResponse:
    if entry is None:
        return JSONResponse({'status': 'ok', 'notice': None, 'error': error or None})
    return JSONResponse({
        'status': 'ok',
        'notice': {
            'post_uuid': entry.get('post_uuid'),
            'start_time': entry.get('start_time'),
            'end_time': entry.get('end_time'),
            'official_period': entry.get('official_period'),
            'modified_on': entry.get('modified_on'),
            'fetched_at': entry.get('fetched_at') or int(time.time()),
        },
        'source': source,
        'error': error or None,
    })


async def maintenance(request: Request):
    """GET /api/maintenance — cached maintenance notice with the poll state machine.

    While the cached period is still valid the official endpoints are hit at
    most once per script start (a fresh list fetch that overwrites the cache
    when a new/changed notice exists); afterwards the cache is served as-is
    until the period expires.
    """
    ui_language = request.query_params.get('language', 'zh-CN')
    now = int(time.time())
    async with _cache_lock:
        cache = _load_cache()
        active = cache is not None and cache.get('end_time', 0) > now

        headers, effective_language = _request_headers(ui_language)
        try:
            if cache is None or not active:
                # No cache, or the cached period already ended: use the list
                # endpoint again to look for a new notice.
                entry = await _discover(headers)
                if entry is None:
                    _clear_cache()
                    return _response(None, 'list')
                entry['language'] = effective_language
                entry['fetched_at'] = now
                _save_cache(entry)
                return _response(entry, 'list')

            # Valid cached notice.  Refresh only on the first request after
            # this script start (fetched_at predates the process), then serve
            # the cache unchanged until the period expires.
            if cache.get('fetched_at', 0) >= PROCESS_START_TIME:
                return _response(cache, 'cache')

            entry = await _discover(headers)
            if entry is None:
                # List unavailable or no notice found: keep the last good
                # cache; the period itself still governs visibility.
                return _response(cache, 'cache')
            entry['language'] = effective_language
            entry['fetched_at'] = now
            _save_cache(entry)
            return _response(entry, 'list')
        except Exception as exc:
            logger.warning(f'Maintenance notice refresh failed: {exc}')
            if cache is not None:
                return _response(cache, 'cache')
            return _response(None, 'error', str(exc))
