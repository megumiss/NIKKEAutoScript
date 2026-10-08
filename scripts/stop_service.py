"""Stop only the backend belonging to this checkout, and wait for cleanup."""

import json
import os
import signal
from pathlib import Path

import psutil


def stop_backend(root=None, timeout=60):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    directory = root / 'config' / '.virtual_display'
    marker = directory / 'backend.json'
    record = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else None
    targets = []
    launchers = []
    owner = None
    if record:
        try:
            candidate = psutil.Process(record['pid'])
            if abs(candidate.create_time() - record['created']) < 0.01:
                if Path(candidate.cwd()).resolve() != root:
                    raise RuntimeError('Backend working directory does not match its ownership marker')
                owner = candidate
        except psutil.NoSuchProcess:
            pass
    ancestors = {process.pid for process in owner.parents()} if owner is not None else set()
    for process in psutil.process_iter(['pid', 'cmdline', 'create_time']):
        try:
            if process.pid == os.getpid() or Path(process.cwd()).resolve() != root:
                continue
            command = process.info['cmdline'] or []
            backend_command = any(Path(arg).name == 'gui.py' for arg in command)
            backend_command = backend_command or (record and process.pid == record['pid'] and any(
                'multiprocessing.spawn' in arg for arg in command
            ))
            if not backend_command:
                continue
            # With EnableReload, gui.py supervises the marked spawn process.
            # Let that launcher observe the child's clean exit before waiting for it.
            if process.pid in ancestors and any(Path(arg).name == 'gui.py' for arg in command):
                launchers.append(process)
                continue
            if record and (process.pid != record['pid'] or abs(process.create_time() - record['created']) > 0.01):
                raise RuntimeError('A backend is running but its ownership marker does not match')
            targets.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    if owner is not None and owner.pid not in {process.pid for process in targets}:
        raise RuntimeError('Live backend ownership could not be verified; refusing to report a successful stop')
    for process in targets:
        process.send_signal(signal.SIGTERM)
    _, alive = psutil.wait_procs(targets, timeout=timeout)
    if alive:
        raise RuntimeError('Backend has not exited; refusing to report a successful stop')
    _, alive = psutil.wait_procs(launchers, timeout=5)
    if alive:
        raise RuntimeError('Backend launcher has not exited')
    if marker.exists():
        result = json.loads(marker.read_text(encoding='utf-8'))
        if result.get('cleanup_errors'):
            raise RuntimeError('Backend exited with incomplete display cleanup: ' + '; '.join(result['cleanup_errors']))
    if list(directory.glob('display-*.json')) or list(directory.glob('worker-*.json')):
        raise RuntimeError('Backend resources remain unverified; cleanup is not complete')
    return len(targets)


if __name__ == '__main__':
    try:
        stop_backend()
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc))
