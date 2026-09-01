"""与实例无关的全局工具。"""

import asyncio
import os
import re

import yaml
from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.deep import deep_get
from module.config.utils import filepath_argument, read_file, write_file
from module.daemon.update_hosts import HOSTS_PATH, UpdateHosts, read_section
from module.logger import logger


SHORTCUTS_FILE = './config/shortcuts.yaml'
SHORTCUTS_TEMPLATE = './config/shortcuts.template.yaml'
SHORTCUT_KEYS = ('UPDATE', 'START', 'STOP', 'RESTART', 'ROTATE', 'DEV_TOOLS', 'REFRESH', 'HARD_REFRESH')
SHORTCUT_DEFAULTS = {
    'UPDATE': 'F8', 'START': 'F9', 'STOP': 'F10', 'RESTART': 'F11', 'ROTATE': 'Ctrl+F12',
    'DEV_TOOLS': 'Ctrl+Shift+I', 'REFRESH': 'Ctrl+R', 'HARD_REFRESH': 'Ctrl+Shift+R',
}
SHORTCUT_MODIFIERS = {'CTRL': 'Ctrl', 'ALT': 'Alt', 'SHIFT': 'Shift', 'SUPER': 'Super'}
SHORTCUT_NAMED_KEYS = {
    'BACKSPACE': 'Backspace', 'DELETE': 'Delete', 'END': 'End', 'ENTER': 'Enter', 'ESCAPE': 'Escape',
    'HOME': 'Home', 'INSERT': 'Insert', 'PAGEDOWN': 'PageDown', 'PAGEUP': 'PageUp', 'SPACE': 'Space',
    'TAB': 'Tab', 'ARROWDOWN': 'ArrowDown', 'ARROWLEFT': 'ArrowLeft', 'ARROWRIGHT': 'ArrowRight',
    'ARROWUP': 'ArrowUp',
}


def _default_hosts():
    # 默认段落与实例版 Hosts 修改共用 argument.yaml 中的定义，避免两处漂移
    argument = read_file(filepath_argument('argument'))
    return deep_get(argument, 'Hosts.Hosts.value', '') or ''


def _parse_sections(block):
    """
    把默认 hosts 模板按 '# 区服名' 注释头拆成分段；注释掉的记录行去掉 '# ' 前缀，
    是否默认生效以模板中是否注释为准。首个分段（通用）始终生效。
    """
    sections = []
    current = None
    for raw in str(block).splitlines():
        line = raw.strip()
        if not line:
            continue
        body = line.lstrip('#').strip() if line.startswith('#') else None
        # 段落头：注释且去掉 '#' 后不是 'IP 域名' 形式
        if body is not None and not re.match(r'^\d+\.\d+\.\d+\.\d+\s', body):
            current = {'name': body, 'lines': [], 'default_on': False}
            sections.append(current)
            continue
        if current is None:
            current = {'name': '通用', 'lines': [], 'default_on': False}
            sections.append(current)
        if body is None:
            current['lines'].append(line)
            current['default_on'] = True
        else:
            current['lines'].append(body)
    for index, section in enumerate(sections):
        section['common'] = index == 0
    return sections


def _active_lines():
    """hosts 文件当前 NKAS 段落中生效（未注释）的记录行；没有段落返回 None。"""
    section = read_section()
    if section is None:
        return None
    return [line.strip() for line in section.splitlines() if line.strip() and not line.strip().startswith('#')]


def _hosts_payload():
    sections = _parse_sections(_default_hosts())
    if not os.path.exists(HOSTS_PATH):
        return {'supported': False, 'applied': False, 'active': [], 'sections': sections}
    active = _active_lines()
    return {
        'supported': True,
        'applied': active is not None,
        'active': active or [],
        'sections': sections,
    }


def _shortcut_defaults():
    try:
        values = read_file(SHORTCUTS_TEMPLATE)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(f'读取快捷键模板失败，使用内置默认值：{exc}')
        values = {}
    if not isinstance(values, dict):
        values = {}
    return {key: str(values.get(key) or SHORTCUT_DEFAULTS[key]).strip() for key in SHORTCUT_KEYS}


def _normalize_shortcut(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('快捷键不能为空。')
    parts = [part.strip() for part in value.split('+')]
    if any(not part for part in parts):
        raise ValueError(f'快捷键格式无效：{value}')
    modifiers = []
    key = None
    for part in parts:
        upper = part.upper()
        if upper in SHORTCUT_MODIFIERS:
            modifier = SHORTCUT_MODIFIERS[upper]
            if modifier in modifiers:
                raise ValueError(f'快捷键包含重复修饰键：{value}')
            modifiers.append(modifier)
            continue
        if key is not None:
            raise ValueError(f'快捷键只能包含一个主按键：{value}')
        if re.fullmatch(r'[A-Z0-9]', upper):
            key = upper
        elif re.fullmatch(r'F(?:[1-9]|1[0-9]|2[0-4])', upper):
            key = upper
        else:
            key = SHORTCUT_NAMED_KEYS.get(upper)
            if key is None:
                raise ValueError(f'不支持的快捷键按键：{part}')
    if key is None:
        raise ValueError(f'快捷键缺少主按键：{value}')
    ordered = [name for name in ('Ctrl', 'Alt', 'Shift', 'Super') if name in modifiers]
    return '+'.join([*ordered, key])


def _shortcuts_payload():
    defaults = _shortcut_defaults()
    try:
        saved = read_file(SHORTCUTS_FILE)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(f'读取快捷键配置失败，使用默认值：{exc}')
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    enabled_value = saved.get('ENABLED', True)
    if isinstance(enabled_value, str):
        enabled = enabled_value.strip().lower() not in ('false', '0', 'no', 'off')
    else:
        enabled = bool(enabled_value)
    shortcuts = {}
    for key in SHORTCUT_KEYS:
        try:
            shortcuts[key] = _normalize_shortcut(saved.get(key, defaults[key]))
        except ValueError:
            shortcuts[key] = defaults[key]
    return {'shortcuts': shortcuts, 'defaults': defaults, 'enabled': enabled}


async def hosts_state(_: Request):
    return JSONResponse(_hosts_payload())


async def shortcuts_state(_: Request):
    return JSONResponse(_shortcuts_payload())


async def shortcuts_update(request: Request):
    try:
        data = await request.json()
        raw_shortcuts = data['shortcuts']
    except (KeyError, TypeError, ValueError):
        return JSONResponse({'status': 'error', 'message': 'Expected shortcuts in request body.'}, status_code=400)
    if not isinstance(raw_shortcuts, dict) or set(raw_shortcuts) != set(SHORTCUT_KEYS):
        return JSONResponse({'status': 'error', 'message': '快捷键配置项不完整。'}, status_code=422)
    try:
        shortcuts = {key: _normalize_shortcut(raw_shortcuts[key]) for key in SHORTCUT_KEYS}
    except ValueError as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=422)
    used = {}
    for key, value in shortcuts.items():
        identity = value.casefold()
        if identity in used:
            return JSONResponse(
                {'status': 'error', 'message': f'{used[identity]} 与 {key} 使用了相同的快捷键 {value}。'},
                status_code=422,
            )
        used[identity] = key
    enabled = data.get('enabled', True)
    if not isinstance(enabled, bool):
        return JSONResponse({'status': 'error', 'message': 'enabled 必须为布尔值。'}, status_code=422)
    try:
        write_file(SHORTCUTS_FILE, {'ENABLED': enabled, **shortcuts})
    except OSError as exc:
        logger.exception(exc)
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=500)
    return JSONResponse({'status': 'success', 'shortcuts': shortcuts, 'enabled': enabled, 'restart_required': True})


async def game_clone_info(_: Request):
    from module.tools.game_clone import clone_info, clone_status
    return JSONResponse({**clone_info(), 'job': clone_status()})


async def game_clone_start(request: Request):
    from module.tools.game_clone import GameCloneError, clone_status, start_clone
    try:
        data = await request.json()
    except ValueError:
        data = {}
    try:
        start_clone(data.get('source'), data.get('target'), data.get('suffix'))
    except GameCloneError as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=400)
    return JSONResponse({'status': 'success', 'job': clone_status()})


async def hosts_update(request: Request):
    try:
        data = await request.json()
    except ValueError:
        data = {}
    action = data.get('action')
    if action not in ('add', 'delete'):
        return JSONResponse({'status': 'error', 'message': 'Expected action: add/delete.'}, status_code=400)
    if not os.path.exists(HOSTS_PATH):
        return JSONResponse({'status': 'error', 'message': '当前系统不支持修改 hosts 文件。'}, status_code=400)
    try:
        await asyncio.to_thread(
            UpdateHosts.update_hosts, 'Add' if action == 'add' else 'Delete', str(data.get('hosts') or '')
        )
    except PermissionError:
        return JSONResponse(
            {'status': 'error', 'message': '写入 hosts 失败，需要管理员权限，请以管理员身份运行 NKAS。'},
            status_code=403,
        )
    except OSError as exc:
        logger.exception(exc)
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=500)
    # update_hosts 对非法输入只记日志不抛错，返回写后重读的实际状态
    return JSONResponse(_hosts_payload())
