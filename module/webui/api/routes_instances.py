import json
import os
import re
import sys
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from module.config.utils import deep_get, filepath_config, nkas_instance, nkas_template, read_file
from module.logger import logger
from module.submodule.utils import get_config_mod, load_config
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.api.models import InstanceInfo
from module.webui.process_manager import ProcessManager
from module.webui.setting import State
from module.webui.updater import updater


def _response_error(message, status_code=400):
    return JSONResponse({'status': 'error', 'message': message}, status_code=status_code)


def _is_windows_admin():
    """
    Whether the current process holds administrator privileges.
    Non-Windows platforms and undetectable cases are treated as admin so
    the check never blocks a start it cannot reason about.
    """
    if sys.platform != 'win32':
        return True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return True


def _pc_client_requires_admin(name):
    """
    The PC client (NKAS.Client.Platform == 'win') requires administrator
    privileges to capture and control the game window.
    """
    if _is_windows_admin():
        return False
    platform = deep_get(read_file(filepath_config(name)), keys='NKAS.Client.Platform', default='adb')
    return platform == 'win'


# Lives outside ./config because nkas_instance() treats every *.json there
# as an instance.
REMARKS_FILE = './data/instance_remarks.json'


def _load_remarks():
    try:
        with open(REMARKS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _save_remarks(remarks):
    with open(REMARKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(remarks, f, ensure_ascii=False, indent=2)


async def instances(_: Request):
    result = []
    remarks = _load_remarks()
    for name in nkas_instance():
        manager = ProcessManager.get_manager(name)
        current_task = next_task = None
        try:
            from module.webui.api.routes_tasks import queue_data
            queue = queue_data(name)
            if queue['pending']:
                # current_task stays the raw command key: the SPA rail
                # compares it against task keys to mark the running task.
                current_task = queue['running'][0]['command'] if queue['running'] else None
                # next_task is display-only, so the translated name is enough.
                next_task = queue['pending'][0]['name_i18n']
            elif queue['waiting']:
                next_task = queue['waiting'][0]['name_i18n']
        except (AttributeError, OSError, KeyError) as exc:
            logger.warning(f'Unable to read queue for {name}: {exc}')
        result.append(InstanceInfo(name, manager.state, get_config_mod(name), current_task, next_task, remarks.get(name, '')).dict())
    return JSONResponse(result)


async def remark(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
        data = await request.json()
        text = str(data.get('remark', '')).strip()[:100]
    except InstanceNotFound as exc:
        return _response_error(str(exc), 404)
    except (ValueError, TypeError):
        return _response_error('Expected JSON body with remark.')
    remarks = _load_remarks()
    if text:
        remarks[name] = text
    else:
        remarks.pop(name, None)
    _save_remarks(remarks)
    return JSONResponse({'status': 'success', 'remark': text})


async def start(request: Request):
    name = request.path_params['name']
    names = nkas_instance() if name == 'all' else [name]
    if name != 'all':
        try:
            validate_instance(name)
        except InstanceNotFound as exc:
            return _response_error(str(exc), 404)
        manager = ProcessManager.get_manager(name)
        if manager.alive:
            return _response_error(f'Instance "{name}" is already running.', 409)
        if _pc_client_requires_admin(name):
            logger.warning(f'Instance "{name}" start blocked: PC client requires administrator privileges')
            return JSONResponse({
                'status': 'error', 'code': 'admin_required',
                'message': 'PC client requires NKAS to run as administrator. '
                           'Restart NKAS with "Run as administrator".',
            }, status_code=403)
        manager.start(func=get_config_mod(name), ev=updater.event)
        return JSONResponse({'status': 'success', 'message': f'Instance "{name}" started.'})
    results = []
    for instance in names:
        manager = ProcessManager.get_manager(instance)
        if manager.alive:
            results.append({'instance': instance, 'status': 'skipped', 'message': 'Already running.'})
            continue
        if _pc_client_requires_admin(instance):
            logger.warning(f'Instance "{instance}" start blocked: PC client requires administrator privileges')
            results.append({
                'instance': instance, 'status': 'error', 'code': 'admin_required',
                'message': 'PC client requires administrator privileges.',
            })
            continue
        manager.start(func=get_config_mod(instance), ev=updater.event)
        results.append({'instance': instance, 'status': 'success', 'message': 'Started.'})
    return JSONResponse({'status': 'success', 'results': results})


async def stop(request: Request):
    name = request.path_params['name']
    names = nkas_instance() if name == 'all' else [name]
    if name != 'all':
        try:
            validate_instance(name)
        except InstanceNotFound as exc:
            return _response_error(str(exc), 404)
        manager = ProcessManager.get_manager(name)
        if not manager.alive:
            return _response_error(f'Instance "{name}" is not running.', 409)
        manager.stop()
        return JSONResponse({'status': 'success', 'message': f'Instance "{name}" stopped.'})
    results = []
    for instance in names:
        manager = ProcessManager.get_manager(instance)
        if not manager.alive:
            results.append({'instance': instance, 'status': 'skipped', 'message': 'Not running.'})
            continue
        manager.stop()
        results.append({'instance': instance, 'status': 'success', 'message': 'Stopped.'})
    return JSONResponse({'status': 'success', 'results': results})


async def create(request: Request):
    try:
        data = await request.json()
        name = str(data['name']).strip()
        origin = str(data.get('origin', 'template-nkas'))
    except (ValueError, TypeError, KeyError):
        return _response_error('Expected JSON body with name and optional origin.')
    if not name or name in nkas_instance() or re.search(r'[.\\/:*?"\'<>|]', name) or name.lower().startswith('template'):
        return _response_error('Invalid or already used instance name.')
    if origin not in nkas_instance() + nkas_template():
        return _response_error('Source instance not found.', 404)
    State.config_updater.write_file(name, load_config(origin).read_file(origin), get_config_mod(origin))
    return JSONResponse({'status': 'success', 'name': name}, status_code=201)


async def delete(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return _response_error(str(exc), 404)
    if ProcessManager.get_manager(name).alive:
        return _response_error('Stop the instance before deleting it.', 409)
    path = Path(filepath_config(name, get_config_mod(name)))
    if path.exists():
        path.unlink()
    from module.config.account import _get_account_file
    acc = Path(_get_account_file(name))
    if acc.exists():
        acc.unlink()
    remarks = _load_remarks()
    if remarks.pop(name, None) is not None:
        _save_remarks(remarks)
    return JSONResponse({'status': 'success'})


async def export(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return _response_error(str(exc), 404)
    mod = get_config_mod(name)
    filename = f'{name}.json' if mod == 'nkas' else f'{name}.{mod}.json'
    return FileResponse(filepath_config(name, mod), filename=filename, media_type='application/json')


async def import_config(request: Request):
    try:
        try:
            form = await request.form()
            upload = form['file']
            filename = upload.filename
            content = await upload.read()
        except AssertionError:
            # python-multipart is intentionally not a backend dependency.  A
            # browser client can submit a raw JSON upload with this header.
            filename = request.headers.get('x-nkas-filename', '')
            content = await request.body()
        config = json.loads(content.decode('utf-8'))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _response_error(f'Invalid JSON upload: {exc}')
    parts = filename.split('.')
    if len(parts) == 2:
        name, mod = parts[0], 'nkas'
    elif len(parts) == 3:
        name, mod = parts[0], parts[1]
    else:
        return _response_error('Invalid configuration filename.')
    if not name or re.search(r'[\\/:*?"\'<>|]', name) or name.lower().startswith('template'):
        return _response_error('Invalid instance name.')
    State.config_updater.write_file(name, config, mod)
    return JSONResponse({'status': 'success', 'name': name})
