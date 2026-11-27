import csv
import json
import os
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from filelock import FileLock

from module.config.atomicwrites import atomic_write
from module.logger import logger

DEFAULT_STONE_CSV_PATH = './data/{config_name}/stones.csv'
ADJUSTMENT_BOSS = '手动补记'

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


def _read_csv_rows(path: str) -> List[dict]:
    """在文件锁内读取未经筛选的明细；尚未创建 CSV 时返回空列表。"""
    try:
        with open(path, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def _append_csv_rows(path: str, rows: List[dict]) -> None:
    """在文件锁内追加明细，仅为新文件写一次表头。"""
    with open(path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if f.tell() == 0:
            writer.writeheader()
        writer.writerows(rows)


def _daily_totals(rows: List[dict]) -> dict:
    """按自然日累加同一实例的结算、导入和手动补记，保留有记录但总数为零的日期。"""
    totals = {}
    for row in rows:
        timestamp = _parse_timestamp(row.get('timestamp', ''))
        if timestamp is not None:
            day = timestamp.date().isoformat()
            totals[day] = totals.get(day, 0) + _to_int(row['stone_count'])
    return totals


def _merge_adjustments(rows: List[dict], targets: Optional[Dict[Tuple[str, str], int]] = None) -> List[dict]:
    """每个实例、自然日只保留一条补记；指定目标总数时，用目标减去当天的非补记掉落。

    未指定新总数的旧补记按差额求和，保留首条的位置。无记录日显式填零也应生成一条明细。
    """
    targets = targets or {}
    recorded = {}
    adjustments = {}
    merged = []
    for row in rows:
        timestamp = _parse_timestamp(row.get('timestamp', ''))
        if timestamp is not None:
            key = row['config_name'], timestamp.date().isoformat()
            if row['boss'] == ADJUSTMENT_BOSS and not row.get('screenshot_path'):
                if key in adjustments:
                    first = adjustments[key]
                    first['stone_count'] = str(_to_int(first['stone_count']) + _to_int(row['stone_count']))
                    continue
                row = row.copy()
                adjustments[key] = row
            elif key in targets:
                recorded[key] = recorded.get(key, 0) + _to_int(row['stone_count'])
        merged.append(row)

    for key, target in targets.items():
        delta = target - recorded.get(key, 0)
        if key in adjustments:
            adjustments[key]['stone_count'] = str(delta)
        elif delta or key not in recorded:
            name, day = key
            merged.append(dict(timestamp=f'{day} 00:00:00', config_name=name, boss=ADJUSTMENT_BOSS,
                               stone_count=str(delta), screenshot_path=''))
    return merged


def _write_csv_rows(path: str, rows: List[dict]) -> None:
    """在文件锁内原子替换整份明细，写入失败时不截断原有掉落记录。"""
    with atomic_write(path, overwrite=True, newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _sync_daily_adjustments(path: str, targets: Optional[Dict[Tuple[str, str], int]] = None) -> List[dict]:
    """在文件锁内读取并合并补记；只在出现重复记录或目标差额变化时写回 CSV。"""
    rows = _read_csv_rows(path)
    merged = _merge_adjustments(rows, targets)
    if merged != rows:
        _write_csv_rows(path, merged)
    return merged


@contextmanager
def _locked_stone_csv(path: str):
    """串行化同一 CSV 的结算、编辑与迁移；首次遇到旧版修正文件时将差额转为普通明细。"""
    _ensure_parent_dir(path)
    with FileLock(f'{path}.lock', timeout=5):
        legacy = f'{path}.daily.json'
        if os.path.isfile(legacy):
            with open(legacy, encoding='utf-8') as f:
                overrides = json.load(f)
            if not isinstance(overrides, dict):
                raise ValueError(f'Invalid daily totals: {legacy}')
            targets = {}
            for name, days in overrides.items():
                if not isinstance(days, dict):
                    raise ValueError(f'Invalid daily totals: {legacy}')
                for day, target in days.items():
                    if type(target) is not int or target < 0 or date.fromisoformat(day).isoformat() != day:
                        raise ValueError(f'Invalid daily total: {name}/{day}')
                    targets[name, day] = target
            _sync_daily_adjustments(path, targets)
            os.replace(legacy, legacy + '.bak')
        yield


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
    recorded_at = recorded_at or datetime.now().replace(microsecond=0)
    with _locked_stone_csv(csv_path):
        _append_csv_rows(csv_path, [{
            'timestamp': recorded_at.isoformat(sep=' '), 'config_name': config_name or '', 'boss': boss or '',
            'stone_count': _to_int(stone_count), 'screenshot_path': screenshot_path or '',
        }])
    return True


def load_interception_stone_rows(csv_path: str, config_name: str = '') -> List[dict]:
    csv_path = resolve_stone_csv_path(csv_path, config_name=config_name or 'nkas')
    if not csv_path or not (os.path.isfile(csv_path) or os.path.isfile(f'{csv_path}.daily.json')):
        return []
    with _locked_stone_csv(csv_path):
        rows = _sync_daily_adjustments(csv_path)
    return [
        dict(row, stone_count=_to_int(row['stone_count'])) for row in rows
        if (not config_name or row.get('config_name') == config_name) and _parse_timestamp(row.get('timestamp', ''))
    ]


def set_daily_totals(csv_path: str, name: str, totals: Dict[str, int]) -> None:
    """在同一把文件锁内按已校验的 ISO 日期批量更新补记，一次写回，不改变真实掉落明细。"""
    with _locked_stone_csv(csv_path):
        _sync_daily_adjustments(csv_path, {(name, day): total for day, total in totals.items()})


def set_daily_total(csv_path: str, name: str, day: date, total: int) -> None:
    """兼容单日调用，与批量保存共用同一套补记规则。"""
    set_daily_totals(csv_path, name, {day.isoformat(): total})


def migrate_stone_records(source_csv: str, target_csv: str, name: str, new_name: str) -> None:
    """实例改名时同步 CSV 中的实例名；共享文件只改本实例，分文件时一并移动其原始及补记记录。"""
    if not source_csv or not target_csv or name == new_name:
        return
    source, target = (os.path.normcase(os.path.realpath(path)) for path in (source_csv, target_csv))
    if not (os.path.isfile(source) or os.path.isfile(f'{source}.daily.json')):
        return
    with ExitStack() as locks:
        for path in sorted({source, target}):
            locks.enter_context(_locked_stone_csv(path))
        rows = _read_csv_rows(source)
        moved = [row for row in rows if row['config_name'] == name]
        if not moved:
            return
        if source != target:
            rows = [row for row in rows if row['config_name'] != name]
        for row in moved:
            row['config_name'] = new_name
        if source != target:
            _write_csv_rows(target, _merge_adjustments(_read_csv_rows(target) + moved))
        _write_csv_rows(source, _merge_adjustments(rows))


def interception_stone_stats(csv_path: str, config_name: str) -> dict:
    """从同一份 CSV 生成日历与日、周、月曲线；手动补记只影响一条明细，不覆盖后续掉落。"""
    rows = load_interception_stone_rows(csv_path, config_name)
    path = os.path.abspath(csv_path) if csv_path else ''
    return {
        'csv_path': path, 'save_dir': os.path.dirname(path), 'today': date.today().isoformat(),
        'rows': rows, 'days': _daily_totals(rows),
        'series': {
            'daily': dict(zip(('labels', 'values'), build_daily_series(rows))),
            'weekly': dict(zip(('labels', 'values'), build_weekly_series(rows))),
            'monthly': dict(zip(('labels', 'values'), build_monthly_series(rows))),
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
