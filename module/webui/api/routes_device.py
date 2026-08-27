"""Physical device resolution control buttons on the settings page."""

import asyncio
import os
import subprocess

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.deep import deep_get
from module.logger import logger
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.setting import State


def _adb_binary():
    adb = State.deploy_config.AdbExecutable.replace('\\', '/')
    if os.path.exists(adb):
        return adb
    return next((f for f in [
        './bin/adb/adb.exe',
        './toolkit/Lib/site-packages/adbutils/binaries/adb.exe',
        '/usr/bin/adb',
    ] if os.path.exists(f)), 'adb')


def _apply_resolution(name: str, action: str) -> dict:
    config = State.config_updater.read_file(name)
    serial = str(deep_get(config, 'Emulator.Emulator.Serial', '') or '')
    if not serial or serial == 'auto':
        return {'ok': False, 'message': 'Serial is not set, please configure ADB Serial first'}
    adb = _adb_binary()

    # 无线 adb 可能已断开，先尝试 connect（失败不致命，后续命令会报出真实错误）
    if ':' in serial and not serial.startswith('emulator'):
        subprocess.run([adb, 'connect', serial], timeout=10, capture_output=True)

    commands = {
        'set': [
            ['shell', 'wm', 'size', '720x1280'],
            ['shell', 'wm', 'density', '240'],
            ['shell', 'settings', 'put', 'system', 'accelerometer_rotation', '0'],
            ['shell', 'settings', 'put', 'system', 'user_rotation', '0'],
        ],
        # 手动还原是无状态兜底路径，拿不到任务运行时记录的原值，自动旋转直接恢复为开启
        'reset': [
            ['shell', 'wm', 'size', 'reset'],
            ['shell', 'wm', 'density', 'reset'],
            ['shell', 'settings', 'put', 'system', 'accelerometer_rotation', '1'],
            ['shell', 'settings', 'put', 'system', 'user_rotation', '0'],
        ],
    }[action]
    try:
        for cmd in commands:
            result = subprocess.run([adb, '-s', serial, *cmd], timeout=10, capture_output=True)
            if result.returncode != 0:
                message = result.stderr.decode(errors='replace').strip() or f'adb exited with {result.returncode}'
                return {'ok': False, 'message': message}
        size = subprocess.run(
            [adb, '-s', serial, 'shell', 'wm', 'size'], timeout=10, capture_output=True
        ).stdout.decode(errors='replace').strip()
        return {'ok': True, 'message': size}
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f'[{name}] physical device resolution {action} failed: {e}')
        return {'ok': False, 'message': str(e)}


async def resolution(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
        data = await request.json()
        action = data['action']
        if action not in ('set', 'reset'):
            raise ValueError
    except InstanceNotFound as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=404)
    except (KeyError, ValueError, TypeError):
        return JSONResponse({'ok': False, 'message': 'Expected action to be set or reset.'}, status_code=400)
    result = await asyncio.to_thread(_apply_resolution, name, action)
    return JSONResponse(result, status_code=200 if result['ok'] else 500)


def _list_devices() -> list:
    result = subprocess.run(
        [_adb_binary(), 'devices'], timeout=10, capture_output=True,
    )
    devices = []
    for line in result.stdout.decode(errors='replace').splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        devices.append({'serial': parts[0], 'status': parts[1] if len(parts) > 1 else ''})
    return devices


async def devices(_: Request):
    try:
        result = await asyncio.to_thread(_list_devices)
        return JSONResponse({'ok': True, 'devices': result})
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f'adb devices failed: {e}')
        return JSONResponse({'ok': False, 'message': str(e)}, status_code=500)
