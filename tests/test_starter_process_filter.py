import os
import unittest
from unittest.mock import Mock, patch

from deploy.config import ExecutionError
from deploy.nkas import NKASManager
from deploy.starter import Starter


class ProcessFilterTests(unittest.TestCase):
    def test_current_desktop_pid_is_excluded(self):
        allowed = {r'E:\NKAS\nkas.exe'}
        self.assertFalse(NKASManager.is_target_process(
            r'E:\NKAS\nkas.exe', 1234, allowed, {1234},
        ))

    def test_exact_executable_path_is_required(self):
        allowed = {r'E:\NKAS\nkas.exe'}
        self.assertTrue(NKASManager.is_target_process(
            r'e:\nkas\NKAS.EXE', 1234, allowed, set(),
        ))
        self.assertFalse(NKASManager.is_target_process(
            r'E:\NKAS-copy\nkas.exe', 1234, allowed, set(),
        ))

    def test_python_self_pid_is_excluded(self):
        allowed = {r'E:\NKAS\toolkit\python.exe'}
        self.assertFalse(NKASManager.is_target_process(
            r'E:\NKAS\toolkit\python.exe', os.getpid(), allowed, {os.getpid()},
        ))

    def test_similar_directory_is_not_a_target(self):
        allowed = {r'E:\NKAS\toolkit\python.exe'}
        self.assertFalse(NKASManager.is_target_process(
            r'E:\NKAS-old\toolkit\python.exe', 9876, allowed, set(),
        ))


class StarterPrepareTests(unittest.TestCase):
    @patch('deploy.atomic.atomic_failure_cleanup')
    def test_auto_update_false_skips_git_and_pip_but_still_cleans_processes(self, cleanup):
        starter = Starter.__new__(Starter)
        starter.AutoUpdate = False
        starter.git_update = Mock()
        starter.pip_install = Mock()
        starter.nkas_kill = Mock()

        starter.prepare(desktop_pid=4321)

        cleanup.assert_called_once_with('./config')
        starter.git_update.assert_not_called()
        starter.pip_install.assert_not_called()
        starter.nkas_kill.assert_called_once_with(excluded_pids={4321})

    @patch('deploy.atomic.atomic_failure_cleanup')
    def test_git_failure_records_notice_and_continues_without_pip(self, _cleanup):
        starter = Starter.__new__(Starter)
        starter.AutoUpdate = True
        starter._get_head_commit = Mock(return_value=('before', 'message'))
        starter.git_update = Mock(side_effect=ExecutionError('network unavailable'))
        starter.pip_install = Mock()
        starter._save_auto_update_failed_notice = Mock()
        starter.nkas_kill = Mock()

        starter.prepare(desktop_pid=2468)

        starter._save_auto_update_failed_notice.assert_called_once_with('network unavailable')
        starter.pip_install.assert_not_called()
        starter.nkas_kill.assert_called_once_with(excluded_pids={2468})

    @patch('deploy.atomic.atomic_failure_cleanup')
    def test_dependency_failure_blocks_prepare(self, _cleanup):
        starter = Starter.__new__(Starter)
        starter.AutoUpdate = True
        starter._get_head_commit = Mock(return_value=('before', 'message'))
        starter.git_update = Mock()
        starter.pip_install = Mock(side_effect=ExecutionError('pip failed'))
        starter.nkas_kill = Mock()

        with self.assertRaisesRegex(ExecutionError, 'pip failed'):
            starter.prepare(desktop_pid=1357)

        starter.nkas_kill.assert_not_called()

    def test_prepare_mode_does_not_prompt_on_fatal_error(self):
        starter = Starter.__new__(Starter)
        starter.prepare = Mock(side_effect=ExecutionError('dependency install failed'))

        with patch('builtins.input') as prompt, self.assertRaises(SystemExit) as raised:
            starter.start(desktop_pid=1234, interactive=False)

        self.assertEqual(raised.exception.code, 1)
        prompt.assert_not_called()


if __name__ == '__main__':
    unittest.main()
