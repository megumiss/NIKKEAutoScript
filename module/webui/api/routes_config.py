from datetime import date, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

import module.webui.lang as lang
from module.config.deep import deep_get
from module.config.utils import filepath_args, read_file
from module.webui.setting import State
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.api.service_config import ConfigService


def _label(key, fallback):
    # Some i18n leaves are placeholders whose value equals the key itself
    # (e.g. Storage.Storage.name); treat those as missing.
    text = lang.dic_lang.get(lang.LANG, {}).get(key)
    if not text or text == key:
        return fallback
    return text


def _json_value(value):
    """Convert config values to JSON-safe values without changing their file form."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _field(instance_name, task, group, arg, spec, value):
    widget = spec.get('type', 'input')
    display = spec.get('display', 'show')
    if widget == 'lock':
        # lock 类型值由系统锁定（config_updater 始终用默认值），渲染为禁用的开关
        widget = 'checkbox'
        display = 'disabled'
    options = [
        {'value': _json_value(option), 'label': _label(f'{group}.{arg}.{option}', str(option))}
        for option in spec.get('option', [])
    ]
    data_endpoint = {
        'item_table': f'/api/{{name}}/warehouse',
        'interception_stone_charts': f'/api/{{name}}/interception/stats',
        'interception_stone_import': f'/api/{{name}}/interception/import',
    }.get(widget)
    return {
        'key': f'{task}.{group}.{arg}', 'arg': arg, 'widget': widget,
        # Field translations are intentionally shared across tasks.  This is
        # the same group.arg key convention used by the former PyWebIO UI.
        'title': _label(f'{group}.{arg}.name', arg),
        'help': _label(f'{group}.{arg}.help', ''), 'value': _json_value(value),
        'display': display, 'readonly': display in ('readonly', 'disabled'),
        'options': options, 'validate': spec.get('validate'), 'valuetype': spec.get('valuetype'),
        'unit': spec.get('unit'), 'mode': spec.get('mode'), 'path_picker': spec.get('path_picker'),
        'data_endpoint': data_endpoint.replace('{name}', instance_name) if data_endpoint else None,
    }


async def schema(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    args = read_file(filepath_args('args', 'nkas'))
    menu = read_file(filepath_args('menu', 'nkas'))
    config = State.config_updater.read_file(name)
    tasks = {}
    for task, groups in args.items():
        output_groups = []
        for group, fields in groups.items():
            output_fields = []
            for arg, spec in fields.items():
                # hide_keep: 不在任务页显示，但保留用户已存值（config_update 不重置）
                if spec.get('display') in ('hide', 'hide_keep'):
                    continue
                # Storage is a script-managed record dump, not a user setting;
                # it must not show up as a 任务状态 group on the task page.
                if spec.get('type') == 'storage':
                    continue
                output_fields.append(_field(name, task, group, arg, spec, deep_get(config, f'{task}.{group}.{arg}')))
            if output_fields:
                output_groups.append({
                    'key': group, 'name': _label(f'{group}._info.name', group),
                    'help': _label(f'{group}._info.help', ''),
                    # All groups (including Scheduler with enable/next-run) stay
                    # expanded by default so settings are visible on entry.
                    'collapsed': False, 'fields': output_fields,
                })
        tasks[task] = {
            'name': _label(f'Task.{task}.name', task), 'help': _label(f'Task.{task}.help', ''),
            'groups': output_groups,
        }
    menus = []
    for key, item in menu.items():
        menus.append({
            'key': key, 'name': _label(f'Menu.{key}.name', key), 'page': item.get('page', 'setting'),
            'icon': item.get('icon', '•'),
            'tasks': [{'key': task, 'name': tasks.get(task, {}).get('name', task),
                       'help': tasks.get(task, {}).get('help', '')} for task in item.get('tasks', [])],
        })
    return JSONResponse({'menus': menus, 'tasks': tasks})


async def config(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    return JSONResponse(_json_value(State.config_updater.read_file(name)))


async def patch_config(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
        data = await request.json()
        key = data['key']
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    except (KeyError, ValueError, TypeError):
        return JSONResponse({'status': 'error', 'message': 'Expected key and value JSON fields.'}, status_code=400)
    result = ConfigService().patch(name, key, data.get('value'))
    payload = result.dict()
    payload['status'] = 'success' if result.ok else 'error'
    return JSONResponse(payload, status_code=200 if result.ok else 422 if result.invalid else 500)
