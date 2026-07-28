import sys
import threading
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

import module.webui.lang as lang
from module.logger import logger
from module.webui.setting import State
from module.webui.updater import updater


def _json_error(message, status=400):
    return JSONResponse({'status': 'error', 'message': message}, status_code=status)


async def status(_: Request):
    return JSONResponse({
        'api_version': 2, 'spa_version': '1', 'capabilities': {'spa': True, 'websocket': True},
        'version': _git_version(), 'updater_state': updater.state,
        'theme': State.deploy_config.Theme, 'language': lang.LANG,
    })


async def update_status(_: Request):
    local = updater.get_commit(short_sha1=True)
    upstream = updater.get_commit(f'origin/{updater.Branch}', short_sha1=True)
    history = updater.get_commit(f'origin/{updater.Branch}', n=20, short_sha1=True)
    return JSONResponse({'state': updater.state, 'local': local, 'upstream': upstream, 'history': history or []})


async def remote_status(_: Request):
    from module.webui.remote_access import RemoteAccess
    return JSONResponse({
        'state': RemoteAccess.get_state(), 'entry_point': RemoteAccess.get_entry_point(),
        'enabled': bool(State.deploy_config.EnableRemoteAccess),
    })


async def notices(_: Request):
    """Expose one-shot notices that the old pywebio home page displayed."""
    result = []
    auto_updated = Path('./log/auto_update_notice.json')
    auto_failed = Path('./log/auto_update_failed_notice.json')
    startup = Path('./config/startup_notice.yaml')
    if auto_updated.is_file():
        try:
            import json
            data = json.loads(auto_updated.read_text(encoding='utf-8'))
            result.append({'type': 'success', 'key': 'auto_update', 'data': data})
        except (OSError, ValueError) as exc:
            logger.warning(f'Unable to read update notice: {exc}')
    if auto_failed.is_file():
        try:
            import json
            data = json.loads(auto_failed.read_text(encoding='utf-8'))
            result.append({'type': 'error', 'key': 'auto_update_failed', 'data': data})
        except (OSError, ValueError) as exc:
            logger.warning(f'Unable to read failed update notice: {exc}')
    if startup.is_file():
        try:
            from module.config.utils import read_file
            data = read_file(str(startup))
            dismissed = str(getattr(State.deploy_config, 'StartupNoticeDismissedId', '') or '')
            if isinstance(data, dict) and data.get('id') and data.get('id') != dismissed:
                result.append({'type': 'info', 'key': 'startup', 'data': data})
        except (OSError, ValueError) as exc:
            logger.warning(f'Unable to read startup notice: {exc}')
    return JSONResponse({'notices': result})


async def dismiss_notice(request: Request):
    key = request.path_params['key']
    if key == 'auto_update':
        Path('./log/auto_update_notice.json').unlink(missing_ok=True)
    elif key == 'auto_update_failed':
        Path('./log/auto_update_failed_notice.json').unlink(missing_ok=True)
    elif key == 'startup':
        try:
            from module.config.utils import read_file
            data = read_file('./config/startup_notice.yaml')
            State.deploy_config.StartupNoticeDismissedId = str(data.get('id', ''))
        except (OSError, AttributeError, ValueError) as exc:
            return _json_error(str(exc), 422)
    else:
        return _json_error('Unknown notice.', 404)
    return JSONResponse({'status': 'success'})


def _git_version():
    try:
        import subprocess
        return subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], text=True).strip()
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


async def restart(_: Request):
    if State.restart_event is None:
        return _json_error('Restart functionality is not enabled in the current server configuration.', 503)
    def perform_restart():
        from module.webui.app import clearup
        clearup()
        State.restart_event.set()
    threading.Thread(target=perform_restart, daemon=True).start()
    return JSONResponse({'status': 'success', 'message': 'Restart command received.'})


async def update(_: Request):
    if updater.state in ['checking', 'start', 'wait', 'run update']:
        return _json_error(f'Update already in progress. Current state: {updater.state}', 409)
    updater.run_update()
    return JSONResponse({'status': 'success', 'message': 'Update process initiated.'})


async def rotate(_: Request):
    if not sys.platform.startswith('win'):
        return _json_error('Screen rotation is only available on Windows.', 501)
    try:
        import win32api
        import win32con
        device = win32api.EnumDisplayDevices(None, 0)
        mode = win32api.EnumDisplaySettings(device.DeviceName, win32con.ENUM_CURRENT_SETTINGS)
        current = mode.DisplayOrientation
        if current not in (0, 1):
            return _json_error(f'Current orientation {current} not supported.')
        orientation = 1 if current == 0 else 0
        if (current + orientation) % 2 == 1:
            mode.PelsWidth, mode.PelsHeight = mode.PelsHeight, mode.PelsWidth
        mode.DisplayOrientation = orientation
        win32api.ChangeDisplaySettingsEx(device.DeviceName, mode)
        return JSONResponse({'status': 'success', 'message': 'Screen rotated.'})
    except (ImportError, OSError) as exc:
        logger.exception(exc)
        return _json_error(str(exc), 500)


async def monitors(_: Request):
    from module.webui.app import _build_screen_number_options
    return JSONResponse(_build_screen_number_options())


async def set_language(request: Request):
    try:
        value = str((await request.json())['language'])
    except (KeyError, TypeError, ValueError):
        return _json_error('Expected language in request body.')
    lang.set_language(value)
    return JSONResponse({'status': 'success', 'language': lang.LANG})


async def set_theme(request: Request):
    try:
        value = str((await request.json())['theme']).lower()
    except (KeyError, TypeError, ValueError):
        return _json_error('Expected theme in request body.')
    if value not in ('dark', 'light'):
        return _json_error('Theme must be dark or light.')
    State.deploy_config.Theme = value
    State.theme = value
    return JSONResponse({'status': 'success', 'theme': value})
