import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from module.config.config_updater import ConfigUpdater
from module.device.adb.virtual_display_session import DisplaySessions
from module.exception import RequestHumanTakeover


class FakeBridge:
    created = 0

    def __init__(self, record, persist, cancelled=lambda: False):
        self.record, self.persist, self.cancelled = record, persist, cancelled
        self.serial = record['serial']
        self.closed = False

    def device_identity(self):
        return self.serial, 'boot'

    def command(self, args):
        return ''

    def _virtual_display_start(self):
        self.created += 1
        self.record.update(display_id=17, port=10000)
        self.persist(self.record)

    def check_cancelled(self):
        if self.cancelled():
            raise RequestHumanTakeover('cancelled')

    def probe(self):
        return dict(self.record, generation=self.record['socket'], width=720, height=1280, rotation=0)

    def close(self):
        self.closed = True

    def remove_forward(self):
        pass


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = DisplaySessions(self.temp.name, FakeBridge)
        self.target = dict(identity='abc123def456', config_name='test', serial='device-a',
                           adb_binary='adb', package='com.example.game')

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def test_reuse_keeps_screen_and_generation(self):
        first = self.manager.acquire(self.target)
        second = self.manager.acquire(self.target)
        self.assertEqual(first['generation'], second['generation'])
        self.assertEqual(self.manager.sessions[self.target['identity']].created, 1)

    def test_same_device_package_and_duplicate_identity_are_rejected(self):
        self.manager.acquire(self.target)
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            self.manager.acquire(dict(self.target, config_name='copy'))
        with self.assertRaisesRegex(RuntimeError, 'owned'):
            self.manager.acquire(dict(self.target, config_name='other', identity='xyz123def456'))
        self.assertEqual(len(self.manager.sessions), 1)

    def test_cancelled_request_does_not_destroy_retained_screen(self):
        first = self.manager.acquire(self.target)
        with self.assertRaises(RequestHumanTakeover):
            self.manager.acquire(self.target, lambda: True)
        self.assertEqual(self.manager.resolve(self.target)['generation'], first['generation'])

    def test_retained_bridge_does_not_keep_previous_worker_cancellation(self):
        stopped = threading.Event()
        self.manager.acquire(self.target, stopped.is_set)
        stopped.set()
        self.manager.sessions[self.target['identity']].check_cancelled()
        stopped.clear()
        self.manager.acquire(self.target, stopped.is_set)
        stopped.set()
        self.manager.sessions[self.target['identity']].check_cancelled()

    def test_owner_lock_can_be_released_from_shutdown_thread(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.manager.prepare).result()
        self.manager.close()
        another = DisplaySessions(self.temp.name, FakeBridge)
        try:
            another.prepare()
        finally:
            another.close()

    def test_live_worker_prevents_display_shutdown(self):
        self.manager.acquire(self.target)
        self.manager.track_worker('test', os.getpid())
        try:
            with self.assertRaisesRegex(RuntimeError, 'Worker has not exited'):
                self.manager.close()
            self.assertFalse(self.manager.sessions[self.target['identity']].closed)
        finally:
            self.manager.worker_path('test').unlink()

    def test_control_keeps_original_target_after_configuration_change(self):
        first = self.manager.acquire(self.target)
        current = self.manager.resolve_instance('test', False, 'new123def456')
        self.assertEqual(current['generation'], first['generation'])

    def test_creation_failure_rolls_back_only_new_session(self):
        self.manager.acquire(self.target)
        with patch.object(FakeBridge, '_virtual_display_start', side_effect=OSError('startup failed')):
            with self.assertRaises(OSError):
                self.manager.acquire(dict(self.target, identity='new123def456', config_name='new', serial='device-b'))
        self.assertEqual(list(self.manager.sessions), ['abc123def456'])
        self.assertEqual(len(list(Path(self.temp.name).glob('display-*.json'))), 1)

    def test_uncertain_cleanup_keeps_record(self):
        self.manager.acquire(self.target)
        with patch.object(FakeBridge, 'close', side_effect=OSError('device unreachable')):
            with self.assertRaises(OSError):
                self.manager.release_instance('test')
        self.assertEqual(len(list(Path(self.temp.name).glob('display-*.json'))), 1)

    def test_rename_retains_identity_and_delete_releases(self):
        first = self.manager.acquire(self.target)
        self.manager.rename('test', 'renamed')
        self.assertEqual(self.manager.resolve(self.target)['generation'], first['generation'])
        self.manager.release_instance('renamed')
        self.assertFalse(self.manager.sessions)

    def test_record_can_be_adopted_after_owner_restart(self):
        first = self.manager.acquire(self.target)
        self.manager.owner_lock.release()
        self.manager.owner_lock = None
        next_owner = DisplaySessions(self.temp.name, FakeBridge)
        try:
            self.assertEqual(next_owner.acquire(self.target)['generation'], first['generation'])
            self.assertEqual(next_owner.sessions[self.target['identity']].created, 0)
        finally:
            next_owner.close()


class IdentityConcurrencyTests(unittest.TestCase):
    def test_first_read_is_atomic(self):
        template = Path('config/template.json').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'instance.json'
            file.write_text(template, encoding='utf-8')
            updaters = [ConfigUpdater() for _ in range(4)]
            for updater in updaters:
                _ = updater.args
            barrier = threading.Barrier(4)

            def read(updater):
                barrier.wait(timeout=5)
                return updater.read_file('test')['Emulator']['PhysicalDevice']['VirtualDisplayId']

            with patch('module.config.config_updater.filepath_config', return_value=str(file)):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    identities = list(pool.map(read, updaters))
            self.assertEqual(len(set(identities)), 1)
            saved = json.loads(file.read_text(encoding='utf-8'))
            self.assertEqual(saved['Emulator']['PhysicalDevice']['VirtualDisplayId'], identities[0])
            self.assertNotIn('VirtualDisplayId', saved['NKAS'].get('PhysicalDevice', {}))


if __name__ == '__main__':
    unittest.main()
