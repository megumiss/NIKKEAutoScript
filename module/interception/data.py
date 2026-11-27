import csv
import json
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from filelock import FileLock

from module.config.atomicwrites import atomic_write
from module.logger import logger

DEFAULT_STONE_CSV_PATH = './data/{config_name}/stones.csv'

CSV_COLUMNS = [
    'timestamp',
    'config_name',
    'boss',
    'stone_count',
    'screenshot_path',
]


def _ensure_parent_dir(path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def resolve_stone_csv_path(path: str, config_name: str = 'nkas') -> str:
    text = str(path or '').strip()
    if not text:
        return ''
    return text.replace('{config_name}', str(config_name or 'nkas'))


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_timestamp(text: str):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def append_interception_stone_record(
    csv_path: str,
    config_name: str,
    boss: str,
    stone_count: int,
    screenshot_path: str = '',
    recorded_at: datetime = None,
) -> bool:
    csv_path = resolve_stone_csv_path(csv_path, config_name=config_name)
    if not csv_path:
        logger.warning('InterceptionStats: csv_path is empty, skip write.')
        return False

    _ensure_parent_dir(csv_path)
    file_has_content = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0
    recorded_at = recorded_at or datetime.now().replace(microsecond=0)

    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_has_content:
            writer.writeheader()
        writer.writerow(
            {
                'timestamp': recorded_at.isoformat(sep=' '),
                'config_name': config_name or '',
                'boss': boss or '',
                'stone_count': _to_int(stone_count),
                'screenshot_path': screenshot_path or '',
            }
        )
    return True


def load_interception_stone_rows(csv_path: str, config_name: str = '') -> List[dict]:
    csv_path = resolve_stone_csv_path(csv_path, config_name=config_name or 'nkas')
    if not csv_path or not os.path.exists(csv_path):
        return []

    rows: List[dict] = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if config_name and row.get('config_name') != config_name:
                continue
            if _parse_timestamp(row.get('timestamp', '')) is None:
                continue
            row['stone_count'] = _to_int(row.get('stone_count', 0))
            rows.append(row)
    return rows


def _read_daily_overrides(path: str) -> dict:
    """读取按实例和自然日分组的自定义总数；只有文件不存在才视为尚未设置。"""
    try:
        with open(path, encoding='utf-8') as f:
            overrides = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(overrides, dict) or not all(isinstance(days, dict) for days in overrides.values()):
        raise ValueError('Invalid interception daily overrides')
    return overrides


def daily_override_path(csv_path: str) -> str:
    """由已展开实例名的 CSV 路径派生修正文件路径，空路径表示未启用统计。"""
    return f'{csv_path}.daily.json' if csv_path else ''


def set_daily_override(csv_path: str, config_name: str, day: date, total: Optional[int]) -> None:
    """保存已校验的当日总数；None 表示恢复自动统计，0 是有效的自定义总数。

    独立文件不与结算或截图导入争写 CSV。共享路径的实例共用一把锁，避免并发编辑覆盖彼此。
    """
    path = daily_override_path(csv_path)
    _ensure_parent_dir(path)
    with FileLock(f'{path}.lock', timeout=5):
        overrides = _read_daily_overrides(path)
        days = overrides.setdefault(config_name, {})
        key = day.isoformat()
        if total is None:
            if key not in days:
                return
            del days[key]
            if not days:
                del overrides[config_name]
        else:
            days[key] = total
        with atomic_write(path, overwrite=True, encoding='utf-8') as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)


def interception_stone_stats(csv_path: str, config_name: str) -> dict:
    """汇总当前实例的逐日原始数量，并以自定义总数替代对应日期参与日、周、月统计。

    返回的 rows 始终是原始明细；没有结算记录的日期也可以仅凭自定义数量参与统计。
    """
    rows = load_interception_stone_rows(csv_path, config_name)
    override_path = daily_override_path(csv_path)
    overrides = _read_daily_overrides(override_path).get(config_name, {}) if override_path else {}
    days = {day: {'recorded': 0, 'override': count} for day, count in overrides.items()}
    for row in rows:
        day = _parse_timestamp(row['timestamp']).date().isoformat()
        days.setdefault(day, {'recorded': 0, 'override': None})['recorded'] += row['stone_count']
    effective = [
        {'timestamp': day, 'stone_count': data['recorded'] if data['override'] is None else data['override']}
        for day, data in days.items()
    ]
    return {
        'csv_path': os.path.abspath(csv_path) if csv_path else '',
        'override_path': os.path.abspath(override_path) if override_path else '',
        'today': date.today().isoformat(), 'rows': rows, 'days': days,
        'series': {
            'daily': dict(zip(('labels', 'values'), build_daily_series(effective))),
            'weekly': dict(zip(('labels', 'values'), build_weekly_series(effective))),
            'monthly': dict(zip(('labels', 'values'), build_monthly_series(effective))),
        },
    }


def _aggregate_by_bucket(rows: List[dict], buckets: List[date], bucket_of) -> List[int]:
    totals: Dict[date, int] = {bucket: 0 for bucket in buckets}
    for row in rows:
        dt = _parse_timestamp(row.get('timestamp', ''))
        if dt is None:
            continue
        bucket = bucket_of(dt.date())
        if bucket in totals:
            totals[bucket] += _to_int(row.get('stone_count', 0))
    return [totals[bucket] for bucket in buckets]


def build_daily_series(rows: List[dict], days: int = 30) -> Tuple[List[str], List[int]]:
    today = datetime.now().date()
    days = max(1, int(days))
    start = today - timedelta(days=days - 1)
    buckets = [start + timedelta(days=i) for i in range(days)]
    values = _aggregate_by_bucket(rows, buckets, bucket_of=lambda d: d)
    labels = [d.strftime('%m-%d') for d in buckets]
    return labels, values


def build_weekly_series(rows: List[dict], weeks: int = 12) -> Tuple[List[str], List[int]]:
    weeks = max(1, int(weeks))
    today = datetime.now().date()
    this_week_start = today - timedelta(days=today.weekday())
    first_week_start = this_week_start - timedelta(weeks=weeks - 1)
    buckets = [first_week_start + timedelta(weeks=i) for i in range(weeks)]

    def week_start(d: date) -> date:
        return d - timedelta(days=d.weekday())

    values = _aggregate_by_bucket(rows, buckets, bucket_of=week_start)
    labels = [d.strftime('%m-%d') for d in buckets]
    return labels, values


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _shift_month(start: date, delta: int) -> date:
    month0 = start.year * 12 + (start.month - 1) + delta
    year = month0 // 12
    month = month0 % 12 + 1
    return date(year, month, 1)


def build_monthly_series(rows: List[dict], months: int = 12) -> Tuple[List[str], List[int]]:
    months = max(1, int(months))
    this_month = _month_start(datetime.now().date())
    first_month = _shift_month(this_month, -(months - 1))
    buckets = [_shift_month(first_month, i) for i in range(months)]
    values = _aggregate_by_bucket(rows, buckets, bucket_of=_month_start)
    labels = [d.strftime('%Y-%m') for d in buckets]
    return labels, values
