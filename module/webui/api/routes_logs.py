"""Log file viewer over ./log/<date>_<source>.txt.

Files are written by module/logger with the fixed file_formatter
(`YYYY-MM-DD HH:MM:SS.mmm | LEVEL | message`); compact Python tracebacks are
bare continuation lines folded into the record opened by the last levelled
line. Queries filter by level threshold (same debug/info/warn/err ranks as
the live log) and a case-insensitive keyword matched against the whole record,
then return the newest `limit` records.
File names are only taken from the directory listing matched by
FILE_PATTERN, so query params can never escape the log directory.
"""

import os
import re
from collections import deque

from starlette.requests import Request
from starlette.responses import JSONResponse

LOG_DIR = './log'
FILE_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2})_(.+)\.txt$')
LINE_PATTERN = re.compile(
    r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(\.\d{3}) \| '
    r'(DEBUG|INFO|WARNING|ERROR|CRITICAL) \| (.*)$'
)
HR_PATTERN = re.compile(r'^(?P<marker>===|==|--|>)\s+(?P<title>.*?)(?:\s+(?P=marker))?$')
ATTR_PATTERN = re.compile(r'^\[(?P<name>[^\]]+)]\s*(?P<value>.*)$')
LEVEL_RANK = {'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3, 'CRITICAL': 3}
LEVEL_THRESHOLDS = {'debug': 0, 'info': 1, 'warn': 2, 'err': 3}
DEFAULT_LIMIT = 500
MAX_LIMIT = 2000
# Tracebacks can still be long; bound one record so a single entry cannot
# dominate the response.
MAX_RECORD_CHARS = 4000


def _list_files():
    try:
        names = os.listdir(LOG_DIR)
    except OSError:
        return []
    files = []
    for name in names:
        match = FILE_PATTERN.match(name)
        if match and os.path.isfile(os.path.join(LOG_DIR, name)):
            files.append({'date': match.group(1), 'source': match.group(2)})
    files.sort(key=lambda item: (item['date'], item['source']), reverse=True)
    return files


def _scan_file(path, source, threshold, keyword, limit):
    kept = deque(maxlen=limit)
    matched = 0
    record = None

    def flush():
        nonlocal matched
        if record is None:
            return
        _decorate_record(record)
        if record['rank'] < threshold:
            return
        searchable = record['text'] + '\n' + record.get('traceback', '')
        if keyword and keyword not in searchable.lower():
            return
        matched += 1
        kept.append(record)

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for raw in f:
            line = raw.rstrip('\r\n')
            match = LINE_PATTERN.match(line)
            if match:
                flush()
                level = match.group(4)
                record = {
                    'sort': f'{match.group(1)} {match.group(2)}{match.group(3)}',
                    'time': match.group(2),
                    'level': level,
                    'rank': LEVEL_RANK[level],
                    'source': source,
                    'text': match.group(5).rstrip(),
                }
            elif record is not None and len(record['text']) < MAX_RECORD_CHARS:
                record['text'] += f'\n{line}'
        flush()
    return kept, matched


def _decorate_record(record):
    text = record['text'].rstrip()
    traceback_marker = '\nTraceback (most recent call last):'
    if traceback_marker in text:
        text, stack = text.split(traceback_marker, 1)
        record['traceback'] = 'Traceback (most recent call last):' + stack.rstrip()

    hr_match = HR_PATTERN.match(text)
    if hr_match:
        marker = hr_match.group('marker')
        record['kind'] = 'section'
        record['section_level'] = 'major' if marker in ('===', '==') else 'minor'
        record['text'] = hr_match.group('title')
        return

    attr_match = ATTR_PATTERN.match(text)
    if attr_match:
        record['kind'] = 'attr'
        record['attr_name'] = attr_match.group('name')
        record['attr_value'] = attr_match.group('value')
    record['text'] = text


async def log_files(_: Request):
    return JSONResponse({'files': _list_files()})


async def log_query(request: Request):
    params = request.query_params
    date = params.get('date', '')
    source = params.get('source', '')
    threshold = LEVEL_THRESHOLDS.get(params.get('level', ''), 0)
    keyword = params.get('keyword', '').strip().lower()
    try:
        limit = int(params.get('limit', DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = min(max(limit, 1), MAX_LIMIT)

    records = []
    matched = 0
    for item in _list_files():
        if item['date'] != date or (source and item['source'] != source):
            continue
        path = os.path.join(LOG_DIR, f"{item['date']}_{item['source']}.txt")
        kept, count = _scan_file(path, item['source'], threshold, keyword, limit)
        records.extend(kept)
        matched += count
    # Stable sort keeps in-file order for records sharing one timestamp.
    records.sort(key=lambda item: item['sort'])
    records = records[-limit:]
    for item in records:
        del item['sort']
    return JSONResponse({
        'records': records,
        'matched': matched,
        'truncated': matched > len(records),
    })
