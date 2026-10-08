import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.stop_service import stop_backend


class StopServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / 'config' / '.virtual_display'
        self.directory.mkdir(parents=True)

    def process(self, pid, command, created=1):
        process = Mock(pid=pid)
        process.info = {'cmdline': command}
        process.cwd.return_value = str(self.root)
        process.create_time.return_value = created
        process.parents.return_value = []
        return process

    def marker(self, pid, **extra):
        (self.directory / 'backend.json').write_text(json.dumps(dict(pid=pid, created=1, **extra)))

    def test_reload_supervisor_is_waited_after_owned_server_cleanup(self):
        launcher = self.process(100, ['python', 'gui.py'])
        server = self.process(101, ['python', '-c', 'from multiprocessing.spawn import spawn_main'])
        server.parents.return_value = [launcher]
        self.marker(101)
        with patch('scripts.stop_service.psutil.Process', return_value=server), \
                patch('scripts.stop_service.psutil.process_iter', return_value=[launcher, server]), \
                patch('scripts.stop_service.psutil.wait_procs', return_value=([], [])) as wait:
            self.assertEqual(stop_backend(self.root), 1)
        server.send_signal.assert_called_once()
        launcher.send_signal.assert_not_called()
        self.assertEqual(wait.call_args_list[0].args[0], [server])
        self.assertEqual(wait.call_args_list[1].args[0], [launcher])

    def test_unrelated_gui_is_not_signalled(self):
        server = self.process(101, ['python', 'gui.py'])
        other = self.process(102, ['python', 'gui.py'])
        self.marker(101)
        with patch('scripts.stop_service.psutil.Process', return_value=server), \
                patch('scripts.stop_service.psutil.process_iter', return_value=[server, other]):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                stop_backend(self.root)
        server.send_signal.assert_not_called()
        other.send_signal.assert_not_called()

    def test_unverified_live_marker_cannot_report_success(self):
        server = self.process(101, ['python', 'unrelated.py'])
        self.marker(101)
        with patch('scripts.stop_service.psutil.Process', return_value=server), \
                patch('scripts.stop_service.psutil.process_iter', return_value=[server]):
            with self.assertRaisesRegex(RuntimeError, 'ownership could not be verified'):
                stop_backend(self.root)
        server.send_signal.assert_not_called()

    def test_cleanup_errors_and_remaining_records_prevent_success(self):
        with patch('scripts.stop_service.psutil.process_iter', return_value=[]), \
                patch('scripts.stop_service.psutil.wait_procs', return_value=([], [])):
            (self.directory / 'display-test.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'resources remain'):
                stop_backend(self.root)
            (self.directory / 'display-test.json').unlink()
            self.assertEqual(stop_backend(self.root), 0)


if __name__ == '__main__':
    unittest.main()
