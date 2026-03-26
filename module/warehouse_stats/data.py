import csv
import os
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from typing import Dict, List

from module.config.utils import read_file
from module.logger import logger

DEFAULT_ITEM_MAP_PATH = './config/warehouse_items.yaml'
DEFAULT_CSV_PATH = './data/{config_name}/items.csv'

CSV_COLUMNS = [
    'timestamp',
    'item_id',
    'item_name',
    'count',
    'group_id',
    'group_name',
]

SCAN_METHOD_DIRECT = 'direct'
SCAN_METHOD_OPEN_DETAIL = 'detail'


def normalize_scan_method(value) -> str:
    raw = str(value or '').strip().lower()
    if raw in ('', 'direct', 'grid', '直接识别'):
        return SCAN_METHOD_DIRECT
    if raw in ('detail', 'legacy', 'detail', '打开详情识别'):
        return SCAN_METHOD_OPEN_DETAIL
    logger.warning(f'WarehouseStats: Unknown scan_method "{value}", fallback to "{SCAN_METHOD_DIRECT}".')
    return SCAN_METHOD_DIRECT


def _ensure_parent_dir(path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def resolve_csv_path(path: str, config_name: str = 'nkas') -> str:
    text = str(path or '').strip()
    if not text:
        return ''
    return text.replace('{config_name}', str(config_name or 'nkas'))


def ensure_item_map_file(path: str = None) -> str:
    path = path or DEFAULT_ITEM_MAP_PATH
    return path


def ensure_sample_csv(csv_path: str = None, item_map_path: str = None, config_name: str = 'nkas') -> str:
    csv_path = resolve_csv_path(csv_path or DEFAULT_CSV_PATH, config_name=config_name)
    if os.path.exists(csv_path):
        return csv_path

    item_map_path = ensure_item_map_file(item_map_path or DEFAULT_ITEM_MAP_PATH)
    groups = load_item_groups(item_map_path)
    items = flatten_groups(groups)
    if not items:
        logger.warning(f'WarehouseStats: No items found in {item_map_path}, skip sample CSV init.')
        return csv_path

    items_with_counts = []
    for idx, item in enumerate(items, start=1):
        item = item.copy()
        item['count'] = idx * 10
        items_with_counts.append(item)
    write_inventory_csv(csv_path, items_with_counts)
    logger.info(f'WarehouseStats: Initialized sample CSV at {csv_path}')
    return csv_path


def init_warehouse_stats_files(item_map_path: str = None, csv_path: str = None, config_name: str = 'nkas') -> None:
    """
    Initialize sample CSV on startup (does not modify item map).
    """
    item_map_path = ensure_item_map_file(item_map_path or DEFAULT_ITEM_MAP_PATH)
    ensure_sample_csv(csv_path or DEFAULT_CSV_PATH, item_map_path=item_map_path, config_name=config_name)
    # Preload warehouse assets during startup to avoid first-time UI hitch
    # when opening the WarehouseStats item table.
    try:
        from module.warehouse_stats import assets as _warehouse_assets

        _ = _warehouse_assets
    except Exception:
        logger.exception('WarehouseStats: preload assets failed.')


@lru_cache(maxsize=16)
def _load_item_groups_cached(path: str, mtime: float) -> List[dict]:
    _ = mtime
    data = read_file(path)
    if not isinstance(data, dict):
        logger.warning(f'WarehouseStats: Invalid item map format in {path}')
        return []
    groups = data.get('groups', [])
    if not groups:
        logger.warning(f'WarehouseStats: No item groups found in {path}')
        return []

    normalized = []
    for group in groups:
        group_id = str(group.get('id', '')).strip()
        group_name = str(group.get('name', group_id)).strip() or group_id
        items = []
        for item in group.get('items', []):
            item_id = str(item.get('id', '')).strip()
            if not item_id:
                continue
            display_name = str(
                item.get('display_name', item.get('item_name', item.get('label', item.get('title', ''))))
            ).strip()
            items.append(
                {
                    'id': item_id,
                    # Name is the template prefix, e.g. FAVORITE_ITEM_ZWEI
                    'name': str(item.get('name', item_id)).strip(),
                    # Optional UI display name for stats page / csv
                    'display_name': display_name,
                    'scan': item.get('scan', True),
                    'scan_method': normalize_scan_method(item.get('scan_method', SCAN_METHOD_DIRECT)),
                    # Optional scan route key, e.g. page_1 / page_2 / gear / materials
                    # Used only by scanner flow; does not affect stats page grouping.
                    'scan_page': str(item.get('scan_page', 'default')).strip() or 'default',
                    'group_id': group_id,
                    'group_name': group_name,
                }
            )
        normalized.append({'id': group_id, 'name': group_name, 'items': items})

    return normalized


def load_item_groups(path: str = None) -> List[dict]:
    """
    Load item mapping config and normalize into group list.
    Each group contains normalized items with group fields.
    """
    path = ensure_item_map_file(path or DEFAULT_ITEM_MAP_PATH)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f'WarehouseStats: item map file not found: {path}')
    mtime = os.path.getmtime(path)
    # Return a detached copy to prevent accidental in-place mutation on cached data.
    return deepcopy(_load_item_groups_cached(path, mtime))


def flatten_groups(groups: List[dict]) -> List[dict]:
    items: List[dict] = []
    for group in groups:
        for item in group.get('items', []):
            items.append(item)
    return items


def resolve_item_prefix(item: dict) -> str:
    if not item:
        return ''
    return str(item.get('name', '')).strip()


def resolve_item_asset(prefix: str, suffix: str):
    if not prefix:
        return None
    try:
        from module.warehouse_stats import assets
    except Exception:
        return None

    resolved_prefix = prefix
    if suffix == 'ICON':
        # Keep gem-family icons visually consistent.
        if prefix in ('GEM', 'FREE_GEM', 'PAID_GEM'):
            resolved_prefix = 'GEM'
        # Derived voucher entries reuse ADVANCED_RECRUIT_VOUCHER icon in UI.
        elif prefix in ('FREE_GEM_COLOR_VOUCHER', 'ALL_GEM_COLOR_VOUCHER'):
            resolved_prefix = 'ADVANCED_RECRUIT_VOUCHER'

    return getattr(assets, f'{resolved_prefix}_{suffix}', None)


def resolve_item_asset_path(prefix: str, suffix: str) -> str:
    asset = resolve_item_asset(prefix, suffix)
    if asset is None:
        return ''
    return getattr(asset, 'file', '') or ''


def load_latest_counts(csv_path: str) -> Dict[str, dict]:
    """
    Load latest counts for each item_id from csv (last row wins).
    """
    if not csv_path or not os.path.exists(csv_path):
        return {}

    mtime = os.path.getmtime(csv_path)
    return deepcopy(_load_latest_counts_cached(csv_path, mtime))


@lru_cache(maxsize=16)
def _load_latest_counts_cached(csv_path: str, mtime: float) -> Dict[str, dict]:
    _ = mtime
    counts: Dict[str, dict] = {}
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            item_id = row.get('item_id') or ''
            if not item_id:
                continue
            counts[item_id] = row
    return counts


def write_inventory_csv(csv_path: str, items: List[dict], recorded_at: datetime = None) -> int:
    if not csv_path:
        logger.warning('WarehouseStats: csv_path is empty, skip write.')
        return 0

    recorded_at = recorded_at or datetime.now().replace(microsecond=0)
    _ensure_parent_dir(csv_path)

    rows = 0
    # Keep only the latest snapshot for warehouse stats.
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    'timestamp': recorded_at.isoformat(sep=' '),
                    'item_id': item.get('id', ''),
                    'item_name': item.get('name', ''),
                    'count': item.get('count', ''),
                    'group_id': item.get('group_id', ''),
                    'group_name': item.get('group_name', ''),
                }
            )
            rows += 1

    return rows
