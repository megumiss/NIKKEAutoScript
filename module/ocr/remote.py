"""
Worker-side proxy for the OCR sidecar (module/ocr/sidecar.py).

`RemoteOcr` exposes the same `predict` / `recognize` calls as `NIKKEOcr`, so
OCR callers are unchanged. One sidecar process per worker serves all models.
If the sidecar cannot be started or keeps failing, the proxy falls back to the
regular in-process model for the rest of the worker's life.
"""
import atexit
import re
import shlex
import socket
import subprocess
import threading
import time

import numpy as np

from module.logger import logger
from module.ocr.interval import OcrInterval
from module.ocr.progress import OcrInitProgress
from module.ocr.remote_protocol import OcrProtocolError, recv_message, send_message

READY_PATTERN = re.compile(r'NKAS_OCR_READY port=(?P<port>\d+) token=(?P<token>[0-9a-f]+)')
# Covers proot start-up and the `import paddle` that happens inside the sidecar.
READY_TIMEOUT = 180
# The first request also loads the model; later ones take seconds on big cores.
REQUEST_TIMEOUT = 120


class OcrSidecarError(Exception):
    pass


class OcrSidecar:
    def __init__(self, command):
        """
        Args:
            command (tuple[str]): argv that starts module/ocr/sidecar.py
        """
        self.command = tuple(command)
        self._lock = threading.Lock()
        self._process = None
        self._ready = threading.Event()
        self._port = None
        self._token = None
        atexit.register(self.stop)

    @property
    def alive(self):
        return self._process is not None and self._process.poll() is None

    def endpoint(self):
        """Start the sidecar if needed and return (port, token)."""
        with self._lock:
            if self.alive and self._ready.is_set():
                return self._port, self._token
            self._stop_locked()
            self._start_locked()
            return self._port, self._token

    def _start_locked(self):
        logger.info('Start OCR sidecar')
        self._ready.clear()
        try:
            self._process = subprocess.Popen(
                list(self.command),
                # Kept open and never written: the sidecar exits when it reaches EOF.
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', bufsize=1, shell=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except (OSError, ValueError) as e:
            raise OcrSidecarError(f'Cannot run OCR sidecar command: {e}') from e
        threading.Thread(target=self._read_output, args=(self._process,), name='ocr-sidecar-output',
                         daemon=True).start()
        deadline = time.time() + READY_TIMEOUT
        with OcrInitProgress('Starting OCR sidecar'):
            # A wrong command exits immediately; do not wait for the full timeout then.
            while not self._ready.wait(1) and self.alive and time.time() < deadline:
                pass
        if not self._ready.is_set() or not self.alive:
            self._stop_locked()
            raise OcrSidecarError('OCR sidecar exited or did not become ready')

    def _read_output(self, process):
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            match = READY_PATTERN.search(line)
            if match and process is self._process:
                self._port = int(match.group('port'))
                self._token = match.group('token')
                logger.info(f'OCR sidecar ready, port={self._port}')
                self._ready.set()
            else:
                logger.debug(f'OCR sidecar: {line}')
        if process is self._process:
            self._ready.clear()

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        process, self._process = self._process, None
        self._ready.clear()
        if process is None or process.poll() is not None:
            return
        try:
            process.stdin.close()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.warning('OCR sidecar launcher did not exit after kill')


_sidecars = {}
_sidecars_lock = threading.Lock()


def get_sidecar(command):
    """
    Args:
        command (str | list[str]): argv, or a shell-style string (dev_tools/ocr_benchmark.py --sidecar)
    """
    command = tuple(shlex.split(command) if isinstance(command, str) else command)
    with _sidecars_lock:
        if command not in _sidecars:
            _sidecars[command] = OcrSidecar(command)
        return _sidecars[command]


class RemoteOcr:
    def __init__(self, lang, model_type, interval, command):
        self.lang = lang
        self.model_type = model_type
        self._throttle = OcrInterval(interval)
        self._sidecar = get_sidecar(command)
        self._conn = None
        self._warmed = False
        self._local = None

    def check_interval(self):
        self._throttle.check_interval()

    def predict(self, images):
        self.check_interval()
        return self._call('predict', images)

    def recognize(self, images):
        """识别已裁好的单行文本，与 NIKKEOcr.recognize 相同。"""
        self.check_interval()
        return self._call('recognize', images)

    def _call(self, op, images):
        if isinstance(images, (str, bytes)):
            raise TypeError('RemoteOcr takes image arrays, not file paths')
        images = [images] if isinstance(images, np.ndarray) else list(images)
        if self._local is None:
            for attempt in (1, 2):
                try:
                    return self._request(op, images)
                except (OSError, OcrProtocolError, OcrSidecarError) as e:
                    logger.warning(f'OCR sidecar {op} failed (attempt {attempt}/2): {e}')
                    self._close()
                    if isinstance(e, socket.timeout) or not self._sidecar.alive:
                        # Hung or dead: the next attempt starts a fresh process.
                        self._sidecar.stop()
            logger.warning('OCR sidecar unavailable, falling back to in-process OCR')
            from module.ocr.models import create_local_model

            # Throttling already happened in this proxy.
            self._local = create_local_model(self.lang, self.model_type, interval=0)
        return getattr(self._local, op)(images)

    def _connect(self):
        port, token = self._sidecar.endpoint()
        conn = socket.create_connection(('127.0.0.1', port), timeout=REQUEST_TIMEOUT)
        try:
            send_message(conn, {'token': token})
            header, _ = recv_message(conn)
        except (OSError, OcrProtocolError):
            conn.close()
            raise
        if not header.get('ok'):
            conn.close()
            raise OcrSidecarError('OCR sidecar rejected the connection')
        return conn

    def _request(self, op, images):
        if self._conn is None:
            self._conn = self._connect()
        header = {'op': op, 'lang': self.lang, 'model_type': self.model_type}
        if self._warmed:
            send_message(self._conn, header, images)
            reply, _ = recv_message(self._conn)
        else:
            with OcrInitProgress('OCR sidecar loading model'):
                send_message(self._conn, header, images)
                reply, _ = recv_message(self._conn)
        if not reply.get('ok'):
            raise OcrSidecarError(reply.get('error') or 'OCR sidecar request failed')
        self._warmed = True
        return reply['result']

    def _close(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
