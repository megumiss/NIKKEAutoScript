"""Read-only statistics and explicit import actions used by specialised fields."""

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.deep import deep_get
from module.submodule.utils import load_config
from module.webui.api.deps import InstanceNotFound, validate_instance


def _config(name):
    return load_config(name).read_file(name)


async def warehouse(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    from module.warehouse_stats.data import (DEFAULT_CSV_PATH, DEFAULT_ITEM_MAP_PATH, load_item_groups,
                                             load_latest_counts, resolve_csv_path, resolve_item_asset_path,
                                             resolve_item_prefix)
    config = _config(name)
    item_map = deep_get(config, 'WarehouseStats.WarehouseStats.ItemMapPath', DEFAULT_ITEM_MAP_PATH)
    csv_path = resolve_csv_path(deep_get(config, 'WarehouseStats.WarehouseStats.CsvPath', DEFAULT_CSV_PATH), name)
    try:
        groups = load_item_groups(item_map)
    except (OSError, ValueError) as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=422)
    counts = load_latest_counts(csv_path)
    for group in groups:
        for item in group.get('items', []):
            item['count'] = counts.get(item.get('id'), {}).get('count', '')
            asset = resolve_item_asset_path(resolve_item_prefix(item), 'ICON')
            item['icon'] = '/static/' + asset[len('./assets/'):] if asset.startswith('./assets/') else asset
    return JSONResponse({'groups': groups, 'updated_at': max((row.get('timestamp', '') for row in counts.values()), default='')})


async def interception_stats(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    from module.interception.data import (DEFAULT_STONE_CSV_PATH, build_daily_series, build_monthly_series,
                                          build_weekly_series, load_interception_stone_rows, resolve_stone_csv_path)
    config = _config(name)
    csv_path = resolve_stone_csv_path(deep_get(config, 'InterceptionTaskStats.InterceptionDropStats.CsvPath', DEFAULT_STONE_CSV_PATH), name)
    rows = load_interception_stone_rows(csv_path, config_name=name)
    return JSONResponse({'rows': rows, 'series': {
        'daily': dict(zip(('labels', 'values'), build_daily_series(rows))),
        'weekly': dict(zip(('labels', 'values'), build_weekly_series(rows))),
        'monthly': dict(zip(('labels', 'values'), build_monthly_series(rows))),
    }})


async def import_interception(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
        import_path = str((await request.json())['path']).strip()
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    except (KeyError, TypeError, ValueError):
        return JSONResponse({'status': 'error', 'message': 'Expected a screenshot directory path.'}, status_code=400)
    if not import_path:
        return JSONResponse({'status': 'error', 'message': 'Screenshot directory path is empty.'}, status_code=422)
    from module.interception.data import DEFAULT_STONE_CSV_PATH
    from module.interception.interception import import_interception_stone_records_from_screenshots
    config = _config(name)
    csv_path = deep_get(config, 'InterceptionTaskStats.InterceptionDropStats.CsvPath', DEFAULT_STONE_CSV_PATH)
    boss = deep_get(config, 'Interception.Interception.Boss', '')
    result = await asyncio.to_thread(import_interception_stone_records_from_screenshots, import_path, csv_path, name, boss)
    return JSONResponse(result, status_code=200 if result.get('ok') else 422)
