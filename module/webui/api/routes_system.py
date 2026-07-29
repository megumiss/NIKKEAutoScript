import asyncio
import os
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
    # run_update blocks for the whole wait-pull-install cycle; run it off the
    # event loop so the SPA can poll the update state while it progresses.
    threading.Thread(target=updater.run_update, daemon=True).start()
    return JSONResponse({'status': 'success', 'message': 'Update process initiated.'})


async def check_update(_: Request):
    """Only check for updates; applying one stays behind POST /api/update."""
    if updater.state in ['checking', 'start', 'wait', 'run update']:
        return _json_error(f'Update already in progress. Current state: {updater.state}', 409)
    threading.Thread(target=updater.check_update, daemon=True).start()
    return JSONResponse({'status': 'success', 'message': 'Update check initiated.'})


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


def _dialog_initial_location(default):
    initialdir, initialfile = '', ''
    if default:
        default = os.path.normpath(default)
        if os.path.isdir(default):
            initialdir = default
        elif os.path.isdir(os.path.dirname(default)):
            initialdir = os.path.dirname(default)
            initialfile = os.path.basename(default)
    return initialdir, initialfile


def _show_path_dialog_win32(payload):
    """Native dialog through pywin32; the bundled toolkit Python has no tkinter."""
    import win32ui

    initialdir, initialfile = _dialog_initial_location(payload['defaultPath'])
    if payload['mode'] == 'directory':
        from win32com.shell import shell, shellcon
        pidl, _, _ = shell.SHBrowseForFolder(
            0, None, payload['title'], shellcon.BIF_RETURNONLYFSDIRS | shellcon.BIF_NEWDIALOGSTYLE)
        if not pidl:
            return ''
        return os.path.normpath(shell.SHGetPathFromIDList(pidl))
    filter_parts = []
    for ext in payload['accept']:
        filter_parts += [f'{ext.lstrip(".").upper()} files (*{ext})', f'*{ext}']
    filter_parts += ['All files (*.*)', '*.*']
    dialog = win32ui.CreateFileDialog(1, None, initialfile or None, 0, '|'.join(filter_parts) + '||')
    dialog.SetOFNTitle(payload['title'])
    if initialdir:
        dialog.SetOFNInitialDir(initialdir)
    try:
        dialog.DoModal()
    except win32ui.error:
        # Cancelled by the user.
        return ''
    return os.path.normpath(dialog.GetPathName())


def _show_path_dialog_tk(payload):
    """tkinter fallback for non-Windows hosts."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    # Keep the dialog above the browser/Electron window it was triggered from.
    root.attributes('-topmost', True)
    try:
        initialdir, initialfile = _dialog_initial_location(payload['defaultPath'])
        if payload['mode'] == 'directory':
            path = filedialog.askdirectory(parent=root, title=payload['title'], initialdir=initialdir or None)
        else:
            filetypes = [(f'{ext.lstrip(".").upper()} files', f'*{ext}') for ext in payload['accept']]
            filetypes.append(('All files', '*.*'))
            path = filedialog.askopenfilename(
                parent=root, title=payload['title'], initialdir=initialdir or None,
                initialfile=initialfile or None, filetypes=filetypes or None)
    finally:
        root.destroy()
    return os.path.normpath(path) if path else ''


def _show_path_dialog(payload):
    """Open a native file dialog on the host; runs in a worker thread.

    The SPA cannot return full filesystem paths from a browser dialog, and
    the Electron bridge only exists inside the Electron shell, so both
    clients use this server-side dialog instead (the server runs locally).
    """
    if sys.platform.startswith('win'):
        return _show_path_dialog_win32(payload)
    return _show_path_dialog_tk(payload)


async def pick_path(request: Request):
    try:
        data = await request.json()
    except ValueError:
        data = {}
    accept = data.get('accept')
    payload = {
        'mode': 'directory' if data.get('mode') == 'directory' else 'file',
        'title': str(data.get('title') or ''),
        'defaultPath': str(data.get('defaultPath') or ''),
        'accept': [str(ext) for ext in accept if str(ext).strip()] if isinstance(accept, list) else [],
    }
    try:
        path = await asyncio.get_running_loop().run_in_executor(None, _show_path_dialog, payload)
    except Exception as exc:
        # tkinter.TclError (no display), ImportError (no tkinter), etc.
        logger.warning(f'Path picker dialog failed: {exc}')
        return _json_error(f'File picker is not available on this host: {exc}', 503)
    if not path:
        return JSONResponse({'ok': True, 'canceled': True})
    return JSONResponse({'ok': True, 'path': path})


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
