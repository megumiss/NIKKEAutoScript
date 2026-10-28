"""
OCR sidecar: runs PaddleOCR in a separate process for the automation worker.

On Android/Termux the worker inherits the app's background cpuset (two little
cores on some ROMs). Launched through `adb shell run-as`, this process hangs
off adbd instead and may use every core. Enabled by the OcrSidecar deploy
setting; see module/ocr/sidecar_launch.py and deploy/termux/ocr_sidecar.sh.

Protocol: module/ocr/remote_protocol.py. The first line on stdout is
`NKAS_OCR_READY port=<port> token=<token>`; the process exits when stdin is
closed, i.e. when the owning OCR service's adb session ends.
"""
import os
import secrets
import socket
import sys
import threading

from module.ocr.models import OcrModel
from module.ocr.remote_protocol import OcrProtocolError, recv_message, send_message, slim_lines, slim_pages

# OCR is CPU bound; parallel requests from a reconnecting worker would only compete.
_model_lock = threading.Lock()
_models = OcrModel(use_sidecar=False)


def _exit_on_stdin_eof():
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except (OSError, ValueError):
        pass
    os._exit(0)


def _handle(header, arrays):
    op = header.get('op')
    if op == 'ping':
        return {'ok': True}
    if op not in ('predict', 'recognize'):
        return {'ok': False, 'error': f'unknown op {op!r}'}
    images = [a.copy() for a in arrays]
    with _model_lock:
        model = _models.get_model_by(lang=header.get('lang', 'ch'), model_type=header.get('model_type', 'mobile'))
        if op == 'predict':
            return {'ok': True, 'result': slim_pages(model.predict(images))}
        return {'ok': True, 'result': slim_lines(model.recognize(images))}


def _serve(conn, token):
    with conn:
        header, _ = recv_message(conn)
        if not secrets.compare_digest(str(header.get('token', '')), token):
            return
        send_message(conn, {'ok': True})
        while True:
            header, arrays = recv_message(conn)
            try:
                reply = _handle(header, arrays)
            except Exception as e:
                # Report model failures to the worker, which falls back to local OCR.
                reply = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
            send_message(conn, reply)


def _serve_quietly(conn, token):
    try:
        _serve(conn, token)
    except (OSError, OcrProtocolError, ValueError):
        # Disconnects end the session; the worker reconnects when it needs OCR again.
        pass


def main():
    token = secrets.token_hex(16)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('127.0.0.1', 0))
    server.listen(4)
    print(f'NKAS_OCR_READY port={server.getsockname()[1]} token={token} pid={os.getpid()}', flush=True)
    threading.Thread(target=_exit_on_stdin_eof, name='stdin-watch', daemon=True).start()
    while True:
        conn, _ = server.accept()
        threading.Thread(target=_serve_quietly, args=(conn, token), daemon=True).start()


if __name__ == '__main__':
    main()
