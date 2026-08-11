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
# as an instance.  Per-instance metadata lives in a single file keyed by
# instance name, each entry carrying its display order and remark:
#     {"nkas": {"order": 0, "remark": "..."}, "nkas2": {"order": 1}}
META_FILE = './data/instance_meta.json'
# Legacy remarks store, merged into META_FILE once and then removed.
LEGACY_REMARKS_FILE = './data/instance_remarks.json'


def _read_meta():
    try:
        with open(META_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_meta():
    """Read the metadata file; on first use migrate the legacy
    instance_remarks.json into it and drop it."""
    meta = _read_meta()
    if meta:
        return meta
    try:
        with open(LEGACY_REMARKS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    migrated = False
    for name, text in data.items():
        if text:
            meta.setdefault(str(name), {})['remark'] = str(text)
            migrated = True
    if migrated:
        _save_meta(meta)
        try:
            os.remove(LEGACY_REMARKS_FILE)
        except OSError:
            pass
    return meta


def _save_meta(meta):
    with open(META_FILE, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _load_remarks():
    remarks = {}
    for name, entry in _load_meta().items():
        if isinstance(entry, dict) and entry.get('remark'):
            remarks[str(name)] = str(entry['remark'])
    return remarks


def _save_remarks(remarks):
    meta = _load_meta()
    for name, text in remarks.items():
        name = str(name)
        entry = meta.get(name)
        if not isinstance(entry, dict):
            entry = {}
            meta[name] = entry
        if text:
            entry['remark'] = str(text)
        else:
            entry.pop('remark', None)
    for name, entry in meta.items():
        if name not in remarks and isinstance(entry, dict):
            entry.pop('remark', None)
    for name in [name for name, entry in meta.items() if isinstance(entry, dict) and not entry]:
        del meta[name]
    _save_meta(meta)


def _load_order():
    ranked = []
    for name, entry in _load_meta().items():
        if isinstance(entry, dict) and isinstance(entry.get('order'), int):
            ranked.append((str(name), entry['order']))
    ranked.sort(key=lambda item: item[1])
    return [name for name, _ in ranked]


def _save_order(order):
    meta = _load_meta()
    for index, name in enumerate(order):
        name = str(name)
        entry = meta.get(name)
        if not isinstance(entry, dict):
            entry = {}
            meta[name] = entry
        entry['order'] = index
    for name, entry in meta.items():
        if name not in order and isinstance(entry, dict):
            entry.pop('order', None)
    for name in [name for name, entry in meta.items() if isinstance(entry, dict) and not entry]:
        del meta[name]
    _save_meta(meta)


def ordered_instances():
    """Instances ordered by the saved manual order; unknown or new instances
    are appended at the end in their filesystem enumeration order."""
    instances = nkas_instance()
    ranked = [name for name in _load_order() if name in instances]
    for name in instances:
        if name not in ranked:
            ranked.append(name)
    return ranked


async def instances(_: Request):
    result = []
    remarks = _load_remarks()
    for name in ordered_instances():
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


async def reorder(request: Request):
    """Persist the manual display order of instances."""
    try:
        data = await request.json()
        names = data['names']
    except (ValueError, TypeError, KeyError):
        return _response_error('Expected JSON body with names array.')
    if not isinstance(names, list):
        return _response_error('Expected JSON body with names array.')
    instances = nkas_instance()
    ordered = [str(name) for name in names if isinstance(name, str) and name in instances]
    for name in instances:
        if name not in ordered:
            ordered.append(name)
    _save_order(ordered)
    return JSONResponse({'status': 'success', 'names': ordered})


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
    order = _load_order()
    if name in order:
        order.remove(name)
        _save_order(order)
    return JSONResponse({'status': 'success'})


def _migrate_instance_assets(name, new_name):
    """Move the per-instance files and directories that carry the instance
    name so a renamed instance keeps its stats, CDK history and log history.
    Every step is best-effort: a failure here must not abort the rename after
    the config file was already moved."""
    # ./data/<name>/ — warehouse and interception stats CSV directory.
    old_data = Path('./data') / name
    new_data = Path('./data') / new_name
    if old_data.is_dir() and not new_data.exists():
        try:
            os.rename(old_data, new_data)
        except OSError as exc:
            logger.warning(f'Unable to migrate data directory {old_data}: {exc}')
    # ./tmp/<name>/ — CDK redeem history and temporary notify screenshots.
    old_tmp = Path('./tmp') / name
    new_tmp = Path('./tmp') / new_name
    if old_tmp.is_dir() and not new_tmp.exists():
        try:
            os.rename(old_tmp, new_tmp)
        except OSError as exc:
            logger.warning(f'Unable to migrate tmp directory {old_tmp}: {exc}')
    # ./log/<date>_<name>.txt — historical instance logs.  Only files whose
    # source segment equals the old name exactly are moved, so e.g. renaming
    # "nkas" never touches "2026-08-01_nkas2.txt".
    log_dir = Path('./log')
    if log_dir.is_dir():
        pattern = re.compile(r'^(\d{4}-\d{2}-\d{2})_' + re.escape(name) + r'\.txt$')
        for old_log in log_dir.iterdir():
            if not old_log.is_file():
                continue
            match = pattern.match(old_log.name)
            if not match:
                continue
            new_log = log_dir / f'{match.group(1)}_{new_name}.txt'
            try:
                os.rename(old_log, new_log)
            except OSError as exc:
                logger.warning(f'Unable to migrate log file {old_log.name}: {exc}')


async def rename(request: Request):
    """Rename an instance: config file, account file, remarks, order, stats
    directories and historical logs are migrated.  The instance must be
    stopped so no running process points at the old name."""
    name = request.path_params['name']
    try:
        validate_instance(name)
        data = await request.json()
        new_name = str(data['name']).strip()
    except InstanceNotFound as exc:
        return _response_error(str(exc), 404)
    except (ValueError, TypeError, KeyError):
        return _response_error('Expected JSON body with name.')
    if not new_name or new_name == name:
        return _response_error('Invalid or already used instance name.')
    if new_name in nkas_instance() or re.search(r'[.\\/:*?"\'<>|]', new_name) or new_name.lower().startswith('template'):
        return _response_error('Invalid or already used instance name.')
    if ProcessManager.get_manager(name).alive:
        return _response_error('Stop the instance before renaming it.', 409)
    mod = get_config_mod(name)
    old_path = Path(filepath_config(name, mod))
    new_path = Path(filepath_config(new_name, mod))
    if not old_path.exists():
        return _response_error(f'Instance "{name}" config not found.', 404)
    if new_path.exists():
        return _response_error('Invalid or already used instance name.')
    os.rename(old_path, new_path)
    from module.config.account import _get_account_file
    old_acc = Path(_get_account_file(name))
    new_acc = Path(_get_account_file(new_name))
    if old_acc.exists():
        try:
            os.rename(old_acc, new_acc)
        except OSError as exc:
            # Best-effort like the asset migration below: the config file is
            # already renamed, so a leftover or colliding .acc file must not
            # abort the rename half-way.
            logger.warning(f'Unable to migrate account file {old_acc}: {exc}')
    _migrate_instance_assets(name, new_name)
    remarks = _load_remarks()
    if name in remarks:
        remarks[new_name] = remarks.pop(name)
        _save_remarks(remarks)
    order = _load_order()
    if name in order:
        order[order.index(name)] = new_name
        _save_order(order)
    ProcessManager.rename_process(name, new_name)
    logger.info(f'Instance "{name}" renamed to "{new_name}"')
    return JSONResponse({'status': 'success', 'name': new_name})


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
