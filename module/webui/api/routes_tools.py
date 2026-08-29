"""与实例无关的全局工具，目前只有系统 hosts 修改。"""

import asyncio
import os
import re

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.deep import deep_get
from module.config.utils import filepath_argument, read_file
from module.daemon.update_hosts import HOSTS_PATH, UpdateHosts, read_section
from module.logger import logger


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


async def hosts_state(_: Request):
    return JSONResponse(_hosts_payload())


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
