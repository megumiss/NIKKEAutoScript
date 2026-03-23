import csv
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

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
    file_exists = os.path.exists(csv_path)
    recorded_at = recorded_at or datetime.now().replace(microsecond=0)

    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
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
