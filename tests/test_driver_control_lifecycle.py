import ctypes
import multiprocessing
import os
import unittest
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

from main import NikkeAutoScript
from module.config.serial_state import SerialConfig
from module.webui.process_manager import ProcessManager

if os.name == 'nt':
    from module.device.win.virtual_mouse import input as driver_input


class Scheduler(NikkeAutoScript):
    def __init__(self):
        self.config_name = 'driver_test'
        self.is_first_task = False
        self.failure_record = {}
        self.stop_event = Mock()
        self.stop_event.is_set.return_value = False
        self.test_config = SimpleNamespace(
            Client_Platform='win', PCClientInfo_ControlScheme='driver',
            INDEPENDENT_TASKS=['CheckIn'], INDEPENDENT_TASKS_UNDER=['check_in'],
            PCClient_ScreenRotate=False, PCClient_DisableVoice=False, Vdd_VddScreen=False,
            Optimization_ScriptPath='unused.ps1', start_watching=Mock(), should_reload=Mock(return_value=False),
        )

    @property
    def config(self):
        return self.test_config


def mutex_worker(name, pipe):
    driver_input.SCHEME_MUTEX_NAME = name
    try:
        while True:
            command = pipe.recv()
            if command == 'claim':
                pipe.send(driver_input.claim_scheme_mutex())
            elif command == 'release':
                driver_input.release_scheme_mutex()
                pipe.send(True)
            elif command == 'exit':
                return
    finally:
        driver_input.release_scheme_mutex()
        pipe.close()


def scheduler_worker(name, pipe):
    driver_input.SCHEME_MUTEX_NAME = name
    script = Scheduler()
    script.stop_event = None
    script.config.start_watching.side_effect = lambda: pipe.send('waiting')
    try:
        with patch('module.config.serial_state.read_serial_config', return_value=SerialConfig()):
            pipe.send('ready')
            result = script.driver_wait_turn('Reward')
            pipe.send((result, driver_input.scheme_mutex_owned()))
            pipe.recv()
    finally:
        driver_input.release_scheme_mutex()
        pipe.close()


@unittest.skipUnless(os.name == 'nt', 'Windows driver mutex')
class DriverLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.script = Scheduler()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(driver_input, 'SCHEME_MUTEX_NAME', f'Local\\NKAS.Test.{uuid.uuid4().hex}'))
        stack.enter_context(patch('module.config.serial_state.read_serial_config', return_value=SerialConfig()))
        self.mouse = stack.enter_context(patch.object(driver_input, 'shared_mouse')).return_value
        self.addCleanup(driver_input.release_scheme_mutex)

    def cached_device(self):
        handler = driver_input.VirtualMouseInput.__new__(driver_input.VirtualMouseInput)
        handler.mouse_driver = self.mouse
        device = Mock(input_handler=handler)
        self.script.__dict__['device'] = device
        return device

    def test_idle_release_and_cached_device_reacquisition(self):
        device = self.cached_device()
        self.assertTrue(self.script.driver_wait_turn('Reward'))
        self.assertTrue(self.script.wait_until(datetime.now() - timedelta(seconds=2)))
        self.assertFalse(driver_input.scheme_mutex_owned())
        self.mouse.close.assert_called_once()
        self.assertIs(self.script.device, device)
        self.assertTrue(self.script.driver_wait_turn('Reward'))
        self.assertTrue(driver_input.scheme_mutex_owned())

    def test_release_does_not_depend_on_serial_or_backend(self):
        self.assertTrue(driver_input.claim_scheme_mutex())
        with patch('module.config.serial_state.backend_alive', return_value=False):
            self.script.driver_release()
        self.assertFalse(driver_input.scheme_mutex_owned())

    def test_cached_driver_remains_protected_after_config_change(self):
        self.cached_device()
        self.script.config.PCClientInfo_ControlScheme = 'postmessage'
        self.assertTrue(self.script.driver_wait_turn('Reward'))
        self.assertTrue(driver_input.scheme_mutex_owned())

    def test_independent_and_other_control_schemes_do_not_claim(self):
        for platform, scheme, task in [('win', 'driver', 'CheckIn'), ('win', 'postmessage', 'Reward'),
                                       ('win', 'pyautogui', 'Reward'), ('adb', 'driver', 'Reward')]:
            with self.subTest(platform=platform, scheme=scheme, task=task):
                self.script.config.Client_Platform = platform
                self.script.config.PCClientInfo_ControlScheme = scheme
                self.assertTrue(self.script.driver_wait_turn(task))
                self.assertFalse(driver_input.scheme_mutex_owned())

    def test_busy_channel_waits_past_old_retry_limit_and_rechecks_task(self):
        with patch.object(driver_input, 'claim_scheme_mutex', side_effect=[False] * 35 + [True]) as claim:
            self.assertFalse(self.script.driver_wait_turn('Reward'))
        self.assertEqual(claim.call_count, 36)
        self.assertEqual(self.script.stop_event.wait.call_count, 35)

    def test_stop_cancels_wait(self):
        self.script.stop_event.is_set.side_effect = [False, True]
        with patch.object(driver_input, 'claim_scheme_mutex', return_value=False):
            with self.assertRaises(SystemExit) as caught:
                self.script.driver_wait_turn('Reward')
        self.assertEqual(caught.exception.code, 0)

    def test_config_change_cancels_wait_without_running_old_task(self):
        self.script.config.should_reload.return_value = True
        with patch.object(driver_input, 'claim_scheme_mutex', return_value=False) as claim:
            self.assertFalse(self.script.driver_wait_turn('Reward'))
        claim.assert_called_once()

    def test_losing_serial_turn_while_waiting_cancels_claim(self):
        with (
            patch('module.config.serial_state.read_serial_config', return_value=SerialConfig(True, ['driver_test'])),
            patch('module.config.serial_state.is_my_turn', side_effect=[True, False]),
            patch.object(driver_input, 'claim_scheme_mutex', return_value=False) as claim,
        ):
            self.assertFalse(self.script.driver_wait_turn('Reward'))
        claim.assert_called_once()

    def test_enabling_serial_while_waiting_rechecks_turn(self):
        with (
            patch('module.config.serial_state.read_serial_config',
                  side_effect=[SerialConfig(), SerialConfig(True, ['driver_test'])]),
            patch('module.config.serial_state.is_my_turn', return_value=False),
            patch.object(driver_input, 'claim_scheme_mutex', return_value=False) as claim,
        ):
            self.assertFalse(self.script.driver_wait_turn('Reward'))
        claim.assert_called_once()

    def test_waiting_for_serial_token_releases_driver(self):
        driver_input.claim_scheme_mutex()
        self.script.serial_report_waiting = Mock()
        self.script.serial_clear_waiting = Mock()
        with (
            patch('module.config.serial_state.read_serial_config', return_value=SerialConfig(True, ['driver_test'])),
            patch('module.config.serial_state.is_my_turn', side_effect=[False, True]),
            patch('main.time.sleep'),
        ):
            self.assertFalse(self.script.serial_wait_turn('Reward'))
        self.assertFalse(driver_input.scheme_mutex_owned())

    def test_api_failure_is_not_reported_as_busy(self):
        with (
            patch.object(driver_input._kernel32, 'CreateMutexW', return_value=None),
            patch.object(ctypes, 'get_last_error', return_value=5),
        ):
            with self.assertRaisesRegex(OSError, 'CreateMutexW'):
                self.script.driver_wait_turn('Reward')
        self.script.stop_event.wait.assert_not_called()

    def test_close_failure_still_releases_mutex(self):
        driver_input.claim_scheme_mutex()
        self.mouse.close.side_effect = OSError('device close failed')
        with self.assertRaises(OSError):
            self.script.driver_release()
        self.assertFalse(driver_input.scheme_mutex_owned())

    def test_every_idle_strategy_finishes_under_lock_before_release(self):
        for method in ['goto_main', 'stay_there', 'close_game', 'run_script', 'unknown']:
            with self.subTest(method=method):
                device = self.cached_device()
                def check_owned(*args, **kwargs):
                    self.assertTrue(driver_input.scheme_mutex_owned())
                self.script.run = Mock(side_effect=check_owned)
                self.script._post_action = Mock(side_effect=check_owned)
                device.app_stop.side_effect = check_owned
                with patch('module.device.win.script_runner.run_script', side_effect=check_owned):
                    self.script.driver_wait_turn('Reward')
                    self.script.idle_cleanup('Reward', method)
                    self.script.wait_until(datetime.now() - timedelta(seconds=2))
                self.assertFalse(driver_input.scheme_mutex_owned())
                self.script._post_action.assert_called_once()
                if method == 'close_game':
                    self.assertNotIn('device', self.script.__dict__)

    def test_idle_reload_cannot_operate_cached_device_after_release(self):
        device = self.cached_device()
        self.script.run = Mock()
        self.script._post_action = Mock()
        with patch('module.device.win.script_runner.run_script') as run_script:
            for method in ['goto_main', 'stay_there', 'close_game', 'run_script']:
                self.script.idle_cleanup('Reward', method)
        self.script.run.assert_not_called()
        self.script._post_action.assert_not_called()
        device.app_stop.assert_not_called()
        run_script.assert_not_called()

    def test_single_task_acquires_before_execution_and_releases_on_error(self):
        self.script.serial_report_due = Mock()
        def run(*args, **kwargs):
            self.assertTrue(driver_input.scheme_mutex_owned())
            raise RuntimeError('task failed')
        self.script.run = Mock(side_effect=run)
        with self.assertRaisesRegex(RuntimeError, 'task failed'):
            self.script.run_once('reward')
        self.assertFalse(driver_input.scheme_mutex_owned())

    def test_single_task_success_releases(self):
        self.script.serial_report_due = Mock()
        self.script.run = Mock(return_value=True)
        self.assertTrue(self.script.run_once('reward', skip_first_screenshot=True))
        self.script.run.assert_called_once_with('reward', skip_first_screenshot=True)
        self.assertFalse(driver_input.scheme_mutex_owned())

    def test_scheduler_does_not_initialize_device_while_waiting(self):
        self.script.get_next_task = Mock(side_effect=['Reward', SystemExit(0)])
        self.script.driver_wait_turn = Mock(return_value=False)
        with patch.object(NikkeAutoScript, 'device', new_callable=PropertyMock) as device:
            with self.assertRaises(SystemExit):
                self.script._loop()
        device.assert_not_called()

    def test_scheduler_wait_reloads_task_before_using_cached_device(self):
        self.cached_device()
        self.script.get_next_task = Mock(side_effect=['Reward', 'Shop', SystemExit(0)])
        original_claim = driver_input.claim_scheme_mutex
        attempts = iter([False, True, True])
        def claim():
            return original_claim() if next(attempts) else False
        def run(command, **kwargs):
            self.assertTrue(driver_input.scheme_mutex_owned())
            return True
        self.script.run = Mock(side_effect=run)
        with patch.object(driver_input, 'claim_scheme_mutex', side_effect=claim):
            with self.assertRaises(SystemExit):
                self.script._loop()
        self.assertEqual(self.script.run.call_args.args[0], 'shop')
        self.script.run.assert_called_once()

    def test_scheduler_exit_releases(self):
        driver_input.claim_scheme_mutex()
        self.script._loop = Mock(side_effect=SystemExit(0))
        with patch('main.logger.set_file_logger'):
            with self.assertRaises(SystemExit):
                self.script.loop()
        self.assertFalse(driver_input.scheme_mutex_owned())

    def test_real_process_handoff_and_crash_recovery(self):
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        worker = context.Process(target=mutex_worker, args=(driver_input.SCHEME_MUTEX_NAME, child))
        worker.start()
        child.close()
        try:
            self.assertTrue(driver_input.claim_scheme_mutex())
            parent.send('claim')
            self.assertTrue(parent.poll(15))
            self.assertFalse(parent.recv())
            self.script.wait_until(datetime.now() - timedelta(seconds=2))
            parent.send('claim')
            self.assertTrue(parent.poll(5))
            self.assertTrue(parent.recv())
            self.assertFalse(driver_input.claim_scheme_mutex())
            parent.send('release')
            self.assertTrue(parent.poll(5))
            self.assertTrue(parent.recv())
            self.assertTrue(self.script.driver_wait_turn('Reward'))
            self.script.driver_release()
            parent.send('claim')
            self.assertTrue(parent.poll(5))
            self.assertTrue(parent.recv())
            worker.kill()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(self.script.driver_wait_turn('Reward'))
        finally:
            if worker.is_alive():
                worker.kill()
                worker.join(5)
            parent.close()

    def test_real_waiting_worker_resumes_after_release(self):
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        worker = context.Process(target=scheduler_worker, args=(driver_input.SCHEME_MUTEX_NAME, child))
        driver_input.claim_scheme_mutex()
        worker.start()
        child.close()
        try:
            self.assertTrue(parent.poll(15))
            self.assertEqual(parent.recv(), 'ready')
            self.assertTrue(parent.poll(5))
            self.assertEqual(parent.recv(), 'waiting')
            self.script.driver_release()
            self.assertTrue(parent.poll(5))
            self.assertEqual(parent.recv(), (False, True))
            self.assertFalse(driver_input.claim_scheme_mutex())
            parent.send('exit')
            worker.join(5)
            self.assertEqual(worker.exitcode, 0)
            self.assertTrue(driver_input.claim_scheme_mutex())
        finally:
            if worker.is_alive():
                worker.kill()
                worker.join(5)
            parent.close()


class WorkerCleanupTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows worker cleanup')
    def test_single_tool_worker_uses_guarded_entry_and_always_releases(self):
        from module.webui import process_manager
        stop = Mock()
        with (
            patch('main.NikkeAutoScript') as script_class,
            patch.object(process_manager, 'get_available_func', return_value=['Reward']),
            patch.object(process_manager, 'set_file_logger'),
            patch.object(process_manager, 'set_func_logger'),
            patch.object(process_manager, 'set_preview_queue'),
            patch('module.device.win.virtual_mouse.driver_mouse.close_shared_mouse', side_effect=OSError('close failed')),
            patch.object(driver_input, 'release_scheme_mutex') as release,
            patch.object(process_manager, 'logger'),
        ):
            ProcessManager.run_process('driver_test', 'Reward', Mock(), Mock(), stop)
            script_class.return_value.run_once.assert_called_once_with('reward', skip_first_screenshot=True)
            self.assertIs(script_class.stop_event, stop)
            release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
