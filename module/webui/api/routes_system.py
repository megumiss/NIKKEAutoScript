import asyncio
import os
import sys
import threading
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

import module.webui.lang as lang
from deploy.utils import DEPLOY_CONFIG, poor_yaml_read
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
        'home_page': State.deploy_config.HomePage,
        'console_enabled': bool(State.deploy_config.ConsoleEnabled),
    })


async def update_status(_: Request):
    local = updater.get_commit(short_sha1=True)
    upstream = updater.get_commit(f'origin/{updater.Branch}', short_sha1=True)
    history = updater.get_commit(f'origin/{updater.Branch}', n=20, short_sha1=True)
    return JSONResponse({
        'state': updater.state, 'error': updater.check_error,
        'local': local, 'upstream': upstream, 'history': history or [],
    })


async def remote_status(_: Request):
    from module.webui.remote_access import RemoteAccess
    return JSONResponse({
        'state': RemoteAccess.get_state(), 'entry_point': RemoteAccess.get_entry_point(),
        'enabled': bool(State.deploy_config.EnableRemoteAccess),
    })


ANNOUNCEMENT_TYPES = {'info', 'warning', 'important'}


def _read_notice_ids() -> set:
    """Read the persisted ids from the deploy file on every call, so the
    result always reflects the latest state on disk.  The in-memory deploy
    config is per-process and can lag behind the file (forked children on
    Linux/Docker, multiple instances sharing one config, manual edits to the
    file), which made announcements re-pop after the ids were persisted."""
    raw = ''
    try:
        raw = str((poor_yaml_read(DEPLOY_CONFIG) or {}).get('ReadNoticeIds') or '')
    except OSError as exc:
        logger.warning(f'Unable to read deploy config for ReadNoticeIds: {exc}')
    return {item.strip() for item in raw.split(',') if item.strip()}


def _load_announcements():
    """Announcements ship with the repo in config/notices.yaml. Startup
    update hard-resets tracked files to upstream, and this is read from disk
    on every request, so the content never goes stale."""
    file = Path('./config/notices.yaml')
    if not file.is_file():
        return []
    try:
        from module.config.utils import read_file
        data = read_file(str(file))
    except (OSError, ValueError) as exc:
        logger.warning(f'Unable to read notices: {exc}')
        return []
    if not isinstance(data, list):
        logger.warning('config/notices.yaml is not a list, skipped')
        return []
    read = _read_notice_ids()
    result = []
    for row in data:
        if not isinstance(row, dict) or not row.get('id'):
            continue
        notice_id = str(row['id'])
        notice_type = str(row.get('type') or 'info')
        result.append({
            'id': notice_id,
            'date': str(row.get('date') or ''),
            'title': str(row.get('title') or ''),
            'type': notice_type if notice_type in ANNOUNCEMENT_TYPES else 'info',
            'content': str(row.get('content') or ''),
            'read': notice_id in read,
        })
    return result


async def notices(_: Request):
    """Transient update result cards, plus repo-shipped announcements."""
    result = []
    auto_updated = Path('./log/auto_update_notice.json')
    auto_failed = Path('./log/auto_update_failed_notice.json')
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
    return JSONResponse({'notices': result, 'announcements': _load_announcements()})


async def dismiss_notice(request: Request):
    key = request.path_params['key']
    if key == 'auto_update':
        Path('./log/auto_update_notice.json').unlink(missing_ok=True)
    elif key == 'auto_update_failed':
        Path('./log/auto_update_failed_notice.json').unlink(missing_ok=True)
    else:
        return _json_error('Unknown notice.', 404)
    return JSONResponse({'status': 'success'})


async def read_announcements(request: Request):
    """Mark announcement ids as read; persisted in deploy config."""
    try:
        data = await request.json()
    except ValueError:
        data = {}
    ids = data.get('ids')
    if not isinstance(ids, list):
        return _json_error('Expected an ids list in request body.')
    known = {item['id'] for item in _load_announcements()}
    read = _read_notice_ids()
    read.update(str(item) for item in ids if str(item) in known)
    State.deploy_config.ReadNoticeIds = ','.join(sorted(read))
    return JSONResponse({'status': 'success', 'read': sorted(read)})


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


async def vdd_status(_: Request):
    try:
        from module.device.win.vdd import vdd_status as _status
        return JSONResponse(await asyncio.to_thread(_status))
    except Exception as exc:
        logger.warning(f'VDD status failed: {exc}')
        return _json_error(str(exc))


async def vdd_set(request: Request):
    action = request.path_params['action']
    if action not in ('enable', 'disable'):
        return _json_error(f'Invalid VDD action: {action}')
    try:
        from module.device.win import vdd
        await asyncio.to_thread(vdd._expect_success, action)
        return JSONResponse({'status': 'success', 'message': f'VDD {action} done.'})
    except Exception as exc:
        logger.exception(exc)
        return _json_error(f'{exc}(请确认 NKAS 以管理员身份运行)')


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

    # Parent the dialog to the window the user just clicked in, so it pops up
    # in front instead of hiding behind the browser.
    try:
        foreground = win32ui.GetForegroundWindow()
    except win32ui.error:
        foreground = None
    foreground_hwnd = foreground.GetSafeHwnd() if foreground else 0
    initialdir, initialfile = _dialog_initial_location(payload['defaultPath'])
    if payload['mode'] == 'directory':
        from win32com.shell import shell, shellcon
        # BIF_NEWDIALOGSTYLE/BIF_EDITBOX are missing from some pywin32 builds;
        # their values are stable Win32 constants.
        flags = shellcon.BIF_RETURNONLYFSDIRS | 0x0040 | 0x0010
        pidl, _, _ = shell.SHBrowseForFolder(foreground_hwnd, None, payload['title'], flags)
        if not pidl:
            return ''
        # 部分 pywin32 版本 SHGetPathFromIDList 返回 bytes（ANSI），优先用 W 版
        get_path = getattr(shell, 'SHGetPathFromIDListW', None) or shell.SHGetPathFromIDList
        path = get_path(pidl)
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        return os.path.normpath(path)
    filter_parts = []
    for ext in payload['accept']:
        filter_parts += [f'{ext.lstrip(".").upper()} files (*{ext})', f'*{ext}']
    filter_parts += ['All files (*.*)', '*.*']
    dialog = win32ui.CreateFileDialog(1, None, initialfile or None, 0, '|'.join(filter_parts) + '||', foreground)
    dialog.SetOFNTitle(payload['title'])
    if initialdir:
        dialog.SetOFNInitialDir(initialdir)
    try:
        result = dialog.DoModal()
    except win32ui.error:
        # Cancelled by the user.
        return ''
    # Cancelling does not always raise: some pywin32 builds return IDCANCEL
    # instead, and GetPathName() then yields the pre-filled file name.
    if result != 1:  # win32con.IDOK
        return ''
    path = os.path.normpath(dialog.GetPathName())
    if not os.path.isabs(path):
        return ''
    return path


def _show_path_dialog_tk(payload):
    """tkinter fallback for non-Windows hosts."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    # Keep the dialog above the desktop or browser window it was triggered from.
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

    The SPA cannot return arbitrary full filesystem paths from a browser
    dialog, so every desktop shell uses this local server-side dialog.
    """
    if sys.platform.startswith('win'):
        return _show_path_dialog_win32(payload)
    return _show_path_dialog_tk(payload)


async def pick_path(request: Request):
    try:
        data = await request.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    accept = data.get('accept')
    payload = {
        'mode': 'directory' if data.get('mode') == 'directory' else 'file',
        'title': data.get('title') if isinstance(data.get('title'), str) else '',
        'defaultPath': data.get('defaultPath') if isinstance(data.get('defaultPath'), str) else '',
        'accept': [ext.strip() for ext in accept if isinstance(ext, str) and ext.strip()]
        if isinstance(accept, list) else [],
    }
    try:
        path = await asyncio.get_running_loop().run_in_executor(None, _show_path_dialog, payload)
    except Exception as exc:
        # tkinter.TclError (no display), ImportError (no tkinter), etc.
        logger.warning(f'Path picker dialog failed: {exc}')
        return JSONResponse({
            'ok': False,
            'canceled': False,
            'path': '',
            'error': f'File picker is not available on this host: {exc}',
        }, status_code=503)
    if not path:
        return JSONResponse({'ok': True, 'canceled': True, 'path': '', 'error': ''})
    # 防御：对话框实现可能返回 bytes（旧版 pywin32 ANSI API），统一转成 str
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    return JSONResponse({'ok': True, 'canceled': False, 'path': path, 'error': ''})


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
