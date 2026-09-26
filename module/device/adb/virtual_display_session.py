"""Backend-owned Android display sessions and the worker's local IPC client."""

import atexit
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import psutil
from filelock import FileLock

from module.config.utils import write_file
from module.device.adb.virtual_display import VirtualDisplayBridge
from module.exception import RequestHumanTakeover
from module.logger import logger


class DisplaySessions:
    def __init__(self, directory='config/.virtual_display', bridge_factory=VirtualDisplayBridge):
        self.directory = Path(directory)
        self.bridge_factory = bridge_factory
        self.sessions = {}
        self.lock = threading.RLock()
        self.owner_lock = None
        self.owner = {'pid': os.getpid(), 'created': psutil.Process().create_time()}

    def prepare(self):
        with self.lock:
            if self.owner_lock is not None:
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            lock = FileLock(str(self.directory / 'owner.lock'), timeout=0, thread_local=False)
            lock.acquire()
            try:
                for path in self.directory.glob('worker-*.json'):
                    record = json.loads(path.read_text(encoding='utf-8'))
                    process = self._matching_process(record)
                    if process is not None:
                        command = process.cmdline()
                        if Path(process.cwd()).resolve() != Path.cwd().resolve() or command != record.get('command'):
                            raise RuntimeError('Previous worker ownership cannot be verified')
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except psutil.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                    path.unlink()
                for path in self.directory.glob('display-*.json'):
                    record = json.loads(path.read_text(encoding='utf-8'))
                    self.sessions[record['identity']] = self.bridge_factory(record, self.persist)
            except BaseException:
                lock.release()
                raise
            self.owner_lock = lock
            write_file(str(self.directory / 'backend.json'), dict(self.owner, cleanup_errors=[]))

    @staticmethod
    def _matching_process(record):
        try:
            process = psutil.Process(record['pid'])
            if abs(process.create_time() - record['created']) < 0.01:
                return process
        except psutil.NoSuchProcess:
            pass
        return None

    def persist(self, record):
        write_file(str(self.directory / f'display-{record["identity"]}.json'), record)

    def worker_path(self, name):
        key = hashlib.sha256(name.encode()).hexdigest()[:24]
        return self.directory / f'worker-{key}.json'

    def track_worker(self, name, pid):
        self.prepare()
        process = psutil.Process(pid)
        write_file(str(self.worker_path(name)), {
            'pid': pid, 'created': process.create_time(), 'command': process.cmdline(), 'owner': self.owner,
        })

    def forget_worker(self, name):
        path = self.worker_path(name)
        if path.exists():
            record = json.loads(path.read_text(encoding='utf-8'))
            if self._matching_process(record) is not None:
                raise RuntimeError('Worker has not exited')
            path.unlink()

    def _discard(self, bridge):
        bridge.close()
        self.sessions.pop(bridge.record['identity'], None)
        (self.directory / f'display-{bridge.record["identity"]}.json').unlink(missing_ok=True)

    def acquire(self, target, cancelled=lambda: False):
        self.prepare()
        identity = str(target['identity'] or '')
        if not re.fullmatch(r'[a-z0-9]{12}', identity):
            raise RuntimeError('Invalid stable virtual display identity')
        with self.lock:
            if cancelled():
                raise RequestHumanTakeover('Display request cancelled')
            probe_record = dict(target, socket=f'nkas-vd-{uuid.uuid4().hex}', owner=self.owner)
            probe = self.bridge_factory(probe_record, self.persist, cancelled)
            uid, boot = probe.device_identity()
            for old in list(self.sessions.values()):
                if old.record['identity'] != identity and old.record['config_name'] == target['config_name']:
                    self._discard(old)
            bridge = self.sessions.get(identity)
            if bridge is not None:
                if bridge.record['config_name'] != target['config_name']:
                    raise RuntimeError('Duplicate stable display identity in different instances')
                same = bridge.record['device_uid'] == uid and bridge.record['package'] == target['package']
                if not same:
                    self._discard(bridge)
                    bridge = None
                else:
                    if bridge.serial != target['serial']:
                        bridge.remove_forward()
                        bridge.serial = target['serial']
                        bridge.record['serial'] = target['serial']
                    bridge.cancelled = cancelled
                    try:
                        result = bridge.probe()
                        if cancelled():
                            raise RequestHumanTakeover('Display request cancelled')
                        bridge.cancelled = lambda: False
                        return result
                    except (OSError, RuntimeError):
                        self._discard(bridge)
                        bridge = None
                    finally:
                        if bridge is not None:
                            bridge.cancelled = lambda: False
            for old in self.sessions.values():
                if old.record['device_uid'] == uid and old.record['package'] == target['package']:
                    raise RuntimeError(f'Game/display is owned by instance {old.record["config_name"]}')
            if f'NIKKE-{identity}' in probe.command(['shell', 'dumpsys display']):
                raise RuntimeError('An unverified display with the same name already exists')
            probe.record.update(device_uid=uid, boot_id=boot)
            self.sessions[identity] = probe
            self.persist(probe.record)
            try:
                probe._virtual_display_start()
                result = probe.probe()
                probe.check_cancelled()
                probe.cancelled = lambda: False
                return result
            except BaseException:
                try:
                    self._discard(probe)
                except Exception as cleanup_error:
                    logger.error(f'Virtual display rollback incomplete: {cleanup_error}')
                raise

    def resolve(self, target):
        self.prepare()
        with self.lock:
            bridge = self.sessions.get(target['identity'])
            if bridge is None:
                raise RuntimeError('Virtual display has not been created; start a task first')
            # Active resources retain their original device even after configuration edits.
            previous, bridge.cancelled = bridge.cancelled, lambda: False
            try:
                return bridge.probe()
            finally:
                bridge.cancelled = previous

    def release_instance(self, name):
        self.prepare()
        with self.lock:
            for bridge in list(self.sessions.values()):
                if bridge.record['config_name'] == name:
                    self._discard(bridge)

    def resolve_instance(self, name, enabled, identity):
        self.prepare()
        with self.lock:
            active = next((b for b in self.sessions.values() if b.record['config_name'] == name), None)
            if active is None and not enabled:
                return None
            return self.resolve({'identity': active.record['identity'] if active else identity})

    def prepare_primary(self, target):
        self.prepare()
        with self.lock:
            self.release_instance(target['config_name'])
            if self.sessions:
                probe = self.bridge_factory(dict(target, socket=''), self.persist)
                uid, _ = probe.device_identity()
                for old in self.sessions.values():
                    if old.record['device_uid'] == uid and old.record['package'] == target['package']:
                        raise RuntimeError(f'Game/display is owned by instance {old.record["config_name"]}')
        return {}

    def rename(self, old_name, new_name):
        self.prepare()
        with self.lock:
            for bridge in self.sessions.values():
                if bridge.record['config_name'] == old_name:
                    bridge.record['config_name'] = new_name
                    self.persist(bridge.record)

    def close(self):
        if self.owner_lock is None:
            return
        errors = []
        with self.lock:
            for path in self.directory.glob('worker-*.json'):
                record = json.loads(path.read_text(encoding='utf-8'))
                if self._matching_process(record) is not None:
                    errors.append('Worker has not exited; display cleanup deferred')
                else:
                    path.unlink()
            if errors:
                write_file(str(self.directory / 'backend.json'), dict(self.owner, cleanup_errors=errors))
                raise RuntimeError('; '.join(errors))
            for bridge in list(self.sessions.values()):
                try:
                    self._discard(bridge)
                except Exception as exc:
                    errors.append(str(exc))
                    logger.error(f'Virtual display cleanup incomplete ({bridge.record["config_name"]}): {exc}')
            self.owner_lock.release()
            self.owner_lock = None
            write_file(str(self.directory / 'backend.json'), dict(self.owner, cleanup_errors=errors))
        if errors:
            raise RuntimeError('; '.join(errors))


_sessions = None
_sessions_lock = threading.Lock()
_worker_pipe = None
_worker_stopped = None
_rpc_lock = threading.Lock()


def sessions():
    global _sessions
    with _sessions_lock:
        if _sessions is None:
            _sessions = DisplaySessions()
        return _sessions


def configure_worker(pipe, stopped):
    global _worker_pipe, _worker_stopped
    _worker_pipe, _worker_stopped = pipe, stopped


def check_worker_cancelled():
    if _worker_stopped is not None and _worker_stopped.is_set():
        raise RequestHumanTakeover('Task stopped')


def _request(action, target):
    check_worker_cancelled()
    if _worker_pipe is None:
        manager = sessions()
        return getattr(manager, action)(target)
    with _rpc_lock:
        try:
            _worker_pipe.send((action, target))
            deadline = time.monotonic() + 90
            while not _worker_pipe.poll(0.1):
                check_worker_cancelled()
                if time.monotonic() > deadline:
                    raise RuntimeError('Backend virtual display request timed out')
            result = _worker_pipe.recv()
            check_worker_cancelled()
            if 'error' in result:
                raise RuntimeError(result['error'])
            return result
        except (EOFError, BrokenPipeError, OSError, RuntimeError) as exc:
            raise RequestHumanTakeover(f'Virtual display backend unavailable: {exc}') from exc


def acquire_display(target):
    return _request('acquire', target)


def resolve_display(target):
    return _request('resolve', target)


def prepare_primary_display(target):
    return _request('prepare_primary', target)


def serve_worker(pipe, stopped, alive=lambda: True):
    def cancelled():
        return stopped.is_set() or not alive()

    try:
        while not cancelled():
            if not pipe.poll(0.2):
                continue
            action, target = pipe.recv()
            try:
                manager = sessions()
                if action == 'acquire':
                    result = manager.acquire(target, cancelled)
                elif action == 'prepare_primary':
                    result = manager.prepare_primary(target)
                elif action == 'resolve':
                    result = manager.resolve(target)
                else:
                    raise ValueError('Unknown display operation')
            except Exception as exc:
                logger.warning(f'Virtual display {action} failed: {exc}')
                result = {'error': str(exc) or type(exc).__name__}
            if not cancelled():
                pipe.send(result)
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        pipe.close()


def close_sessions():
    if _sessions is not None:
        _sessions.close()


atexit.register(close_sessions)
