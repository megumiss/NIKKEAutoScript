"""Statistics, per-day corrections and explicit import actions used by specialised fields."""

import asyncio
from datetime import date

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.deep import deep_get
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.setting import State


def _config(name):
    return State.config_updater.read_file(name)


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
    from module.interception.data import (DEFAULT_STONE_CSV_PATH, interception_stone_stats, resolve_stone_csv_path,
                                          set_daily_override)
    config = _config(name)
    configured_path = deep_get(config, 'InterceptionTaskStats.InterceptionDropStats.CsvPath', DEFAULT_STONE_CSV_PATH)
    csv_path = resolve_stone_csv_path(configured_path, name)
    if request.method == 'PATCH':
        if not csv_path:
            return JSONResponse({'message': '未配置掉落数据文件。'}, status_code=422)
        try:
            body = await request.json()
            day = date.fromisoformat(body['date'])
            if day.isoformat() != body['date']:
                raise ValueError('Expected YYYY-MM-DD')
            total = body['total']
        except (KeyError, TypeError, ValueError):
            return JSONResponse({'message': '请选择有效的日期并填写当天总数。'}, status_code=422)
        if day > date.today():
            return JSONResponse({'message': '日期不能晚于今天。'}, status_code=422)
        if total is not None and (type(total) is not int or not 0 <= total <= 2**53 - 1):
            return JSONResponse({'message': '请输入非负整数。'}, status_code=422)
        try:
            await asyncio.to_thread(set_daily_override, csv_path, name, day, total)
        except (OSError, ValueError) as exc:
            return JSONResponse({'message': f'保存自定义总数失败：{exc}'}, status_code=500)
    try:
        stats = await asyncio.to_thread(interception_stone_stats, csv_path, name)
    except (OSError, ValueError) as exc:
        return JSONResponse({'message': f'读取掉落统计失败：{exc}'}, status_code=500)
    return JSONResponse(stats)


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
