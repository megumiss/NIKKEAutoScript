"""A scrcpy 4.1 session owned by one interactive preview connection."""

import asyncio
import hashlib
import json
import os
import secrets
import struct
import subprocess
import threading
from collections import deque
from pathlib import Path

from module.logger import logger

SERVER = Path(__file__).resolve().parents[3] / 'bin' / 'scrcpy' / 'scrcpy-server'
SERVER_VERSION = '4.1'
SERVER_SHA256 = 'deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae'
MAX_PACKET_SIZE = 8 * 1024 * 1024


class ScrcpyError(Exception):
    def __init__(self, code, detail=''):
        super().__init__(detail or code)
        self.code = code


def check_server():
    if not SERVER.is_file():
        raise ScrcpyError('server_missing')
    if hashlib.sha256(SERVER.read_bytes()).hexdigest() != SERVER_SHA256:
        raise ScrcpyError('server_version')


def positive_int(value, default, maximum):
    try:
        number = int(value)
    except (ValueError, TypeError, OverflowError):
        return default
    return min(number, maximum) if number > 0 else default


async def run_adb(adb, *args):
    # Uvicorn may use a Windows selector loop without subprocess support.
    # Finish the bounded command before cancellation so it cannot outlive cleanup.
    task = asyncio.create_task(asyncio.to_thread(
        subprocess.run, [adb, *args], capture_output=True, timeout=15,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    ))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise
    if result.returncode:
        raise ScrcpyError('adb_failed', result.stderr.decode('utf-8', errors='replace').strip())
    return result.stdout.decode('utf-8', errors='replace').strip()


async def resolve_serial(adb, serial):
    if serial and serial != 'auto':
        if ':' in serial and not serial.startswith('emulator-'):
            await run_adb(adb, 'connect', serial)
        if await run_adb(adb, '-s', serial, 'get-state') != 'device':
            raise ScrcpyError('device_offline')
        return serial
    output = await run_adb(adb, 'devices')
    devices = [line.split()[0] for line in output.splitlines()[1:]
               if len(line.split()) == 2 and line.split()[1] == 'device']
    if len(devices) != 1:
        raise ScrcpyError('serial_required' if devices else 'device_offline')
    return devices[0]


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid control value')
    return value


def control_packet(message):
    """Validate browser input and serialize the supported 4.1 control messages."""
    kind = message['type']
    if kind == 'touch':
        action = integer(message['action'], 0, 2)
        pointer = integer(message['pointer'], 0, 0x7fffffff)
        width = integer(message['width'], 1, 65535)
        height = integer(message['height'], 1, 65535)
        x = integer(message['x'], 0, width - 1)
        y = integer(message['y'], 0, height - 1)
        return struct.pack('>BBQiiHHHII', 2, action, pointer, x, y, width, height,
                           0 if action == 1 else 65535, 0, 0)
    if kind == 'key':
        key = integer(message['key'], 0, 288)
        return b''.join(struct.pack('>BBIII', 0, action, key, 0, 0) for action in (0, 1))
    if kind == 'text':
        value = message['text']
        if not isinstance(value, str):
            raise ValueError('Invalid text')
        data = value.encode('utf-8')
        if len(data) > 300:
            raise ValueError('Text too long')
        return struct.pack('>BI', 1, len(data)) + data
    if kind == 'clipboard_get':
        return b'\x08\x00'
    if kind in ('paste', 'clipboard_set'):
        value = message['text']
        if not isinstance(value, str):
            raise ValueError('Invalid clipboard text')
        data = value.encode('utf-8')
        if len(data) > 65536:
            raise ValueError('Clipboard text too long')
        sequence = integer(message['sequence'], 1, 0x7fffffff) if kind == 'clipboard_set' else 0
        paste = kind == 'paste' or message.get('paste') is True
        return struct.pack('>BQBI', 9, sequence, int(paste), len(data)) + data
    if kind == 'rotate':
        return b'\x0b'
    raise ValueError('Unknown control message')


class ScrcpySession:
    def __init__(self, adb, serial, display_id=0, bitrate=16000000, max_fps=60):
        self.adb = adb
        self.serial = serial
        self.display_id = display_id
        self.bitrate = positive_int(bitrate, 16000000, 64000000)
        self.max_fps = positive_int(max_fps, 60, 120)
        self.scid = secrets.randbits(31)
        self.remote = f'/data/local/tmp/nkas-preview-{self.scid:08x}.jar'
        self.port = None
        self.process = None
        self.log_thread = None
        self.logs = deque(maxlen=12)
        self.writers = []
        self.video = None
        self.control = None
        self.control_reader = None
        self.connected = asyncio.Event()
        self.pending_frames = 0
        self.frame_slots = asyncio.Semaphore(8)

    def _read_logs(self):
        for line in self.process.stdout:
            self.logs.append(line.decode('utf-8', errors='replace').strip())

    async def start(self):
        task = asyncio.create_task(self._start())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # In particular, retain the port returned by an in-flight `forward`
            # so close() can remove it after a browser disconnects during startup.
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _start(self):
        check_server()
        self.serial = await resolve_serial(self.adb, self.serial)
        await run_adb(self.adb, '-s', self.serial, 'push', str(SERVER), self.remote)
        self.port = int(await run_adb(self.adb, '-s', self.serial, 'forward', 'tcp:0',
                                      f'localabstract:scrcpy_{self.scid:08x}'))
        options = (
            f'scid={self.scid:08x} tunnel_forward=true audio=false control=true '
            f'video_codec=h264 max_size=1280 video_bit_rate={self.bitrate} max_fps={self.max_fps} '
            f'display_id={self.display_id} clipboard_autosync=false power_on=false '
            'send_device_meta=false video_codec_options=profile=1,i-frame-interval=2'
        )
        # Closing stdin terminates only this session's Android process, including
        # cancellation while the server is still waiting for its first socket.
        command = (
            f'CLASSPATH={self.remote} app_process / com.genymobile.scrcpy.Server {SERVER_VERSION} {options}'
            ' </dev/null & server_pid=$!; '
            f"trap 'kill $server_pid 2>/dev/null; rm -f {self.remote}' EXIT; read unused"
        )
        self.process = subprocess.Popen(
            [self.adb, '-s', self.serial, 'shell', command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        self.log_thread = threading.Thread(target=self._read_logs, daemon=True)
        self.log_thread.start()
        try:
            await asyncio.wait_for(self._connect(), timeout=15)
        except asyncio.TimeoutError as exc:
            raise ScrcpyError('start_failed', '\n'.join(self.logs)) from exc
        logger.info(f'[scrcpy] Connected {self.serial}, display={self.display_id}, scid={self.scid:08x}')

    async def _connect(self):
        while True:
            writer = None
            try:
                reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
                self.writers.append(writer)
                if await reader.readexactly(1) == b'\0':
                    self.video = reader
                    break
            except (OSError, asyncio.IncompleteReadError):
                pass
            if writer is not None:
                writer.close()
                self.writers.remove(writer)
            if self.process.poll() is not None:
                raise ScrcpyError('start_failed', '\n'.join(self.logs))
            await asyncio.sleep(0.1)
        self.control_reader, self.control = await asyncio.open_connection('127.0.0.1', self.port)
        self.writers.append(self.control)
        codec = await self.video.readexactly(4)
        if codec != b'h264':
            raise ScrcpyError('start_failed', f'Unexpected video codec: {codec!r}; ' + '\n'.join(self.logs))
        self.connected.set()

    async def device_messages(self, websocket):
        await self.connected.wait()
        while True:
            kind = await self.control_reader.readexactly(1)
            if kind == b'\x00':
                length = struct.unpack('>I', await self.control_reader.readexactly(4))[0]
                if length > (1 << 18) - 5:
                    raise ScrcpyError('invalid_control')
                data = await self.control_reader.readexactly(length)
                await websocket.send_json({'type': 'clipboard', 'text': data.decode('utf-8', errors='replace')})
            elif kind == b'\x01':
                sequence = struct.unpack('>Q', await self.control_reader.readexactly(8))[0]
                await websocket.send_json({'type': 'clipboard_ack', 'sequence': sequence})
            else:
                raise ScrcpyError('invalid_control')

    async def stream(self, websocket):
        while True:
            header = await self.video.readexactly(12)
            if header[0] & 0x80:
                # 4.1 session metadata replaces the old one-off size header.
                _, width, height = struct.unpack('>III', header)
                if not (0 < width <= 65535 and 0 < height <= 65535):
                    raise ScrcpyError('invalid_stream')
                await websocket.send_json({'type': 'size', 'width': width, 'height': height})
                continue
            length = struct.unpack_from('>I', header, 8)[0]
            if not 0 < length <= MAX_PACKET_SIZE:
                raise ScrcpyError('invalid_stream')
            data = await self.video.readexactly(length)
            # A bounded receive window also bounds the browser's decoder queue.
            await asyncio.wait_for(self.frame_slots.acquire(), timeout=15)
            self.pending_frames += 1
            await websocket.send_bytes(header + data)

    async def receive(self, websocket):
        while True:
            text = await websocket.receive_text()
            if len(text) > 100000:
                raise ScrcpyError('invalid_control')
            try:
                message = json.loads(text)
                if message['type'] == 'bitrate':
                    if not self.connected.is_set():
                        raise ScrcpyError('invalid_control')
                    return integer(message['value'], 100000, 64000000)
                if message['type'] == 'ack':
                    if self.pending_frames:
                        self.pending_frames -= 1
                        self.frame_slots.release()
                    continue
                data = control_packet(message)
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ScrcpyError('invalid_control') from exc
            if self.control is None:
                raise ScrcpyError('invalid_control')
            self.control.write(data)
            await self.control.drain()

    async def close(self):
        for writer in self.writers:
            writer.close()
        for writer in self.writers:
            try:
                await writer.wait_closed()
            except OSError:
                pass
        self.writers.clear()
        if self.process is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                await asyncio.to_thread(self.process.wait, timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                await asyncio.to_thread(self.process.wait, timeout=3)
            if self.log_thread is not None:
                await asyncio.to_thread(self.log_thread.join, 1)
            self.process.stdout.close()
        if self.port is not None:
            try:
                await run_adb(self.adb, '-s', self.serial, 'forward', '--remove', f'tcp:{self.port}')
            except (ScrcpyError, OSError, subprocess.TimeoutExpired) as exc:
                logger.warning(f'[scrcpy] Failed to remove forward {self.port}: {exc}')
        # Also covers failures between push and launching the shell trap.
        if self.serial and self.serial != 'auto':
            try:
                await run_adb(self.adb, '-s', self.serial, 'shell', 'rm', '-f', self.remote)
            except (ScrcpyError, OSError, subprocess.TimeoutExpired) as exc:
                logger.warning(f'[scrcpy] Failed to remove session file: {exc}')
