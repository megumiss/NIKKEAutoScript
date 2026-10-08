"""Screenshot previews and same-origin scrcpy video/control sessions."""

import asyncio
import subprocess

from starlette.responses import JSONResponse, Response
from starlette.websockets import WebSocketDisconnect

from module.device.adb.scrcpy import ScrcpyError, ScrcpySession, check_server
from module.logger import logger
from module.webui.api.deps import InstanceNotFound, load_instance_config
from module.webui.api.routes_device import _adb_binary
from module.webui.api.service_config import ConfigService
from module.webui.process_manager import ProcessManager
from module.webui.security_entry import same_origin

_sessions = set()


async def screenshot(request):
    name = request.path_params['name']
    manager = ProcessManager._processes.get(name)
    preview = manager.latest_preview if manager else None
    if preview is None:
        return JSONResponse({'error': 'no preview'}, status_code=404)
    captured_at, data = preview
    return Response(content=data, media_type='image/jpeg', headers={'X-Captured-At': str(captured_at)})


def _context(name):
    config = load_instance_config(name)
    if config.Client_Platform != 'adb':
        raise ScrcpyError('win_platform')
    check_server()
    serial = str(config.Emulator_Serial or 'auto').strip()
    manager = ProcessManager._processes.get(name)
    source = manager.preview_source if manager and manager.alive else None
    if source and serial not in ('auto', source['serial']):
        source = None
    if source:
        serial = source['serial']
    virtual = config.PhysicalDevice_Enable and config.PhysicalDevice_VirtualDisplay
    if virtual and (not source or not source.get('display_id')):
        # Never silently control the main display when the game runs elsewhere.
        raise ScrcpyError('virtual_display_unavailable')
    display_id = source['display_id'] if virtual else 0
    return config, serial, display_id


async def scrcpy(request):
    try:
        _context(request.path_params['name'])
    except InstanceNotFound:
        return JSONResponse({'available': False, 'reason': 'instance_missing'})
    except ScrcpyError as exc:
        return JSONResponse({'available': False, 'reason': exc.code})
    return JSONResponse({'available': True})


async def scrcpy_socket(websocket):
    if not same_origin(websocket):
        await websocket.close(code=4403)
        return
    name = websocket.path_params['name']
    await websocket.accept()
    session = None
    tasks = []
    owned = False
    try:
        config, serial, display_id = _context(name)
        if name in _sessions:
            raise ScrcpyError('busy')
        _sessions.add(name)
        owned = True
        session = ScrcpySession(_adb_binary(), serial, display_id, config.Scrcpy_Bitrate, config.Scrcpy_MaxFps)

        async def stream():
            await session.start()
            await websocket.send_json({'type': 'settings', 'bitrate': session.bitrate})
            await session.stream(websocket)

        receiver = asyncio.create_task(session.receive(websocket))
        tasks = [asyncio.create_task(stream()), receiver, asyncio.create_task(session.device_messages(websocket))]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if receiver in done:
            bitrate = receiver.result()
            result = await asyncio.to_thread(ConfigService().patch, name, 'Emulator.Scrcpy.Bitrate', bitrate)
            if not result.ok:
                raise ScrcpyError('settings_failed', result.message)
            # The client waits for our close frame, sent after session cleanup,
            # before reconnecting. This avoids racing the old session's lock.
            await websocket.send_json({'type': 'restart'})
    except WebSocketDisconnect:
        pass
    except (InstanceNotFound, ScrcpyError, OSError, subprocess.TimeoutExpired, asyncio.IncompleteReadError,
            asyncio.TimeoutError) as exc:
        code = exc.code if isinstance(exc, ScrcpyError) else 'connection_failed'
        logger.warning(f'[{name}] scrcpy {code}: {exc}')
        try:
            await websocket.send_json({'type': 'error', 'code': code})
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            if session:
                await session.close()
        finally:
            if owned:
                _sessions.discard(name)
            try:
                await websocket.close()
            except (OSError, RuntimeError, WebSocketDisconnect):
                pass
