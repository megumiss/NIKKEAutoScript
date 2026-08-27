"""Interactive web console: run shell commands over a WebSocket.

Three handshake gates, all evaluated per connection against the live
State.deploy_config (never cached), so toggling the switch applies without a
restart; refusing to accept leaves the feature invisible:
1. ConsoleEnabled must be on.
2. The request Host header (host part only, port ignored) must be in
   ConsoleAllowHosts.
3. If an Origin header is present, its host must match the Host header,
   blocking cross-site WebSocket hijacking from malicious pages in the local
   browser. A missing Origin is allowed through: browsers always send Origin
   on fetch/WS, and bare TCP clients can forge the Host header anyway, so the
   allowlist is the only line of defense there.
"""

import asyncio
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from module.logger import logger
from module.webui.api.routes_device import _adb_binary
from module.webui.setting import State

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Live console connections; closed by close_all_consoles() when the feature
# switch flips off.
_active_consoles = set()


def close_all_consoles():
    # Each socket's own finally block kills its process tree.
    for websocket in list(_active_consoles):
        try:
            asyncio.get_running_loop().create_task(websocket.close(code=4403))
        except RuntimeError:
            pass


def _header_host(value: str) -> str:
    """Host part of a Host header or origin netloc, port stripped;
    '[::1]:12271' yields '::1', a bare IPv6 literal is returned unchanged."""
    value = value.strip()
    if value.startswith('['):
        end = value.find(']')
        if end != -1:
            return value[1:end]
    if value.count(':') == 1:
        return value.rsplit(':', 1)[0]
    return value


def _allowed_hosts(config) -> set:
    return {item.lower() for item in re.split(r'[,\s]+', config.ConsoleAllowHosts or '') if item}


def _kill_process_tree(process: subprocess.Popen):
    try:
        if sys.platform.startswith('win'):
            # taskkill /T kills the whole tree; plain terminate() would orphan
            # grandchildren such as `python main.py` spawned through cmd.
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass


def _decode(data: bytes) -> str:
    # Child output encoding varies per command on Windows: git/python emit
    # UTF-8 while cmd builtins emit GBK, so try UTF-8 first and fall back.
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('gbk', errors='replace')


def _start_command(command: str, loop, events) -> subprocess.Popen:
    tokens = command.split(None, 1)
    if tokens[0] == 'adb':
        # Reuse the deploy-config adb path instead of relying on PATH.
        command = ' '.join([f'"{_adb_binary()}"'] + tokens[1:])
    if sys.platform.startswith('win'):
        args, kwargs = ['cmd', '/c', command], {}
    else:
        # Own process group so the whole tree dies with one killpg.
        args, kwargs = ['/bin/sh', '-c', command], {'start_new_session': True}
    # Binary pipes: decoding is deferred to _decode so each line can pick
    # UTF-8 or GBK (a line never splits a multibyte sequence in either).
    process = subprocess.Popen(
        args, cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)

    def reader(stream, name):
        try:
            for line in stream:
                text = _decode(line).rstrip('\r\n')
                loop.call_soon_threadsafe(events.put_nowait, ('output', name, text))
        except (OSError, ValueError):
            pass

    def waiter():
        code = process.wait()
        loop.call_soon_threadsafe(events.put_nowait, ('exit', code))

    threading.Thread(target=reader, args=(process.stdout, 'stdout'), daemon=True).start()
    threading.Thread(target=reader, args=(process.stderr, 'stderr'), daemon=True).start()
    threading.Thread(target=waiter, daemon=True).start()
    return process


async def _receive_loop(websocket: WebSocket, events):
    try:
        while True:
            try:
                message = await websocket.receive_json()
            except ValueError:
                # Not JSON; ignore and keep the connection alive.
                continue
            events.put_nowait(('msg', message))
    except (WebSocketDisconnect, RuntimeError, ConnectionClosed, OSError):
        events.put_nowait(('closed', None))


async def console_socket(websocket: WebSocket):
    config = State.deploy_config
    if not config.ConsoleEnabled:
        await websocket.close(code=4403)
        return
    host = _header_host(websocket.headers.get('host', ''))
    if host.lower() not in _allowed_hosts(config):
        await websocket.close(code=4403)
        return
    origin = websocket.headers.get('origin', '')
    if origin and _header_host(urlsplit(origin).netloc).lower() != host.lower():
        await websocket.close(code=4403)
        return
    await websocket.accept()
    _active_consoles.add(websocket)
    loop = asyncio.get_running_loop()
    events = asyncio.Queue()
    receiver = asyncio.ensure_future(_receive_loop(websocket, events))
    process = None
    try:
        while True:
            kind, *payload = await events.get()
            if kind == 'closed':
                break
            if kind == 'output':
                await websocket.send_json({'type': 'output', 'stream': payload[0], 'data': payload[1]})
            elif kind == 'exit':
                process = None
                await websocket.send_json({'type': 'exit', 'code': payload[0]})
            elif kind == 'msg':
                message = payload[0]
                if not isinstance(message, dict):
                    continue
                if message.get('type') == 'start':
                    command = str(message.get('command') or '').strip()
                    if not command:
                        continue
                    # One process per connection; reject start while busy.
                    if process is not None:
                        await websocket.send_json({'type': 'error', 'message': 'A command is already running.'})
                        continue
                    try:
                        process = _start_command(command, loop, events)
                    except OSError as exc:
                        logger.warning(f'Console command failed to start: {exc}')
                        await websocket.send_json({'type': 'error', 'message': str(exc)})
                elif message.get('type') == 'stop':
                    if process is not None:
                        _kill_process_tree(process)
    except (WebSocketDisconnect, RuntimeError, ConnectionClosed, OSError):
        pass
    finally:
        _active_consoles.discard(websocket)
        receiver.cancel()
        if process is not None:
            _kill_process_tree(process)
