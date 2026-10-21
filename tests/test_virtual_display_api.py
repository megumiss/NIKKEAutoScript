import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from module.webui.api import routes_instances, routes_preview


class InstanceLifecycleApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = SimpleNamespace(path_params={'name': 'test'})
        self.manager = Mock(alive=False)
        for target, kwargs in [
            ('validate_instance', {}), ('_clear_serial_failed', {}),
            ('_pc_client_requires_admin', {'return_value': False}),
            ('_driver_scheme_missing_driver', {'return_value': False}),
            ('_vdd_missing_driver', {'return_value': False}),
            ('get_config_mod', {'return_value': 'nkas'}),
            ('ProcessManager.get_manager', {'return_value': self.manager}),
        ]:
            patcher = patch(f'module.webui.api.routes_instances.{target}', **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_start_runs_with_real_threadpool_argument_binding(self):
        response = await routes_instances.start(self.request)
        self.assertEqual(response.status_code, 200)
        self.manager.start.assert_called_once_with('nkas', ev=routes_instances.updater.event)

    async def test_stopping_dead_worker_is_idempotent(self):
        for _ in range(2):
            response = await routes_instances.stop(self.request)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.manager.stop.call_count, 2)

    async def test_missing_driver_blocks_threadpool_start(self):
        for check, code in [('_driver_scheme_missing_driver', 'driver_not_installed'),
                            ('_vdd_missing_driver', 'vdd_driver_not_installed')]:
            with self.subTest(code=code), patch(f'module.webui.api.routes_instances.{check}', return_value=True):
                response = await routes_instances.start(self.request)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(json.loads(response.body)['code'], code)
        self.manager.start.assert_not_called()

    async def test_copy_renews_identity_and_respects_schedule_option(self):
        from copy import deepcopy
        original = {'Emulator': {'PhysicalDevice': {'VirtualDisplayId': 'abc123def456'}},
                    'Reward': {'Scheduler': {'Enable': True, 'NextRun': '2026-10-09 00:00:00'}}}
        for keep_schedule in (False, True):
            with self.subTest(keep_schedule=keep_schedule):
                config = deepcopy(original)
                request = SimpleNamespace(json=AsyncMock(return_value={
                    'name': 'copy', 'origin': 'test', 'keep_schedule': keep_schedule,
                }))
                with patch.object(routes_instances, 'nkas_instance', return_value=['test']), \
                        patch.object(routes_instances, 'load_config') as load, \
                        patch.object(routes_instances, '_reset_schedule_to_default') as reset, \
                        patch.object(routes_instances.State, 'config_updater') as updater:
                    load.return_value.read_file.return_value = config
                    response = await routes_instances.create(request)
                self.assertEqual(response.status_code, 201)
                copied = updater.write_file.call_args.args[1]
                self.assertNotEqual(copied['Emulator']['PhysicalDevice']['VirtualDisplayId'], 'abc123def456')
                self.assertEqual(reset.call_count, 0 if keep_schedule else 1)

    async def test_unconfirmed_stop_returns_failure(self):
        self.manager.stop.side_effect = RuntimeError('worker still alive')
        response = await routes_instances.stop(self.request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body)['message'], 'worker still alive')


class ControlTargetTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(Client_Platform='adb', Emulator_Serial='configured-device',
                                      PhysicalDevice_Enable=True, PhysicalDevice_VirtualDisplay=True,
                                      PhysicalDevice_VirtualDisplayId='abc123def456')
        self.sessions = Mock()
        self.sessions.resolve_instance.return_value = {
            'identity': 'abc123def456', 'display_id': 17, 'generation': 'session-a',
            'socket': 'nkas-vd-test', 'device_uid': 'uid', 'boot_id': 'boot',
            'serial': 'active-device', 'package': 'com.example.game',
        }
        for target, kwargs in [
            ('module.webui.api.routes_preview.load_instance_config', {'return_value': self.config}),
            ('module.webui.api.routes_preview.check_server', {}),
            ('module.device.adb.virtual_display_session.sessions', {'return_value': self.sessions}),
        ]:
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_scrcpy_uses_retained_session_after_worker_stops(self):
        worker = SimpleNamespace(alive=False, preview_source={'serial': 'stale-device', 'display_id': 3})
        with patch.dict(routes_preview.ProcessManager._processes, {'test': worker}, clear=True):
            result = routes_preview._context('test')
        self.assertEqual(result, (self.config, 'active-device', 17, 'session-a'))

    def test_scrcpy_keeps_active_display_after_configuration_changes(self):
        self.config.PhysicalDevice_VirtualDisplay = False
        self.assertEqual(routes_preview._context('test')[1:], ('active-device', 17, 'session-a'))

    def test_scrcpy_failed_resolution_never_uses_cached_primary_target(self):
        self.sessions.resolve_instance.side_effect = RuntimeError('bridge unreachable')
        with self.assertRaises(routes_preview.ScrcpyError) as result:
            routes_preview._context('test')
        self.assertEqual(result.exception.code, 'virtual_display_unavailable')

    def test_primary_preview_uses_running_worker_serial(self):
        self.config.PhysicalDevice_VirtualDisplay = False
        self.config.Emulator_Serial = 'auto'
        self.sessions.resolve_instance.return_value = None
        worker = SimpleNamespace(alive=True, preview_source={'serial': 'resolved-device', 'display_id': 0})
        with patch.dict(routes_preview.ProcessManager._processes, {'test': worker}, clear=True):
            self.assertEqual(routes_preview._context('test')[1:], ('resolved-device', 0, None))

    def test_failed_managed_resolution_never_returns_primary_target(self):
        manager = Mock()
        manager.resolve_instance.side_effect = RuntimeError('bridge unreachable')
        config = SimpleNamespace(PhysicalDevice_Enable=True, PhysicalDevice_VirtualDisplay=True,
                                 PhysicalDevice_VirtualDisplayId='abc123def456')
        with patch('module.device.adb.virtual_display_session.sessions', return_value=manager):
            target = routes_preview.control_target('test', config)
        self.assertTrue(target['enabled'])
        self.assertFalse(target['available'])
        self.assertNotIn('displayId', target)


if __name__ == '__main__':
    unittest.main()
