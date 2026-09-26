import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.webui.api import routes_instances, routes_preview


class InstanceLifecycleApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = SimpleNamespace(path_params={'name': 'test'})
        self.manager = Mock(alive=False)
        for target, kwargs in [
            ('validate_instance', {}), ('_clear_serial_failed', {}),
            ('_pc_client_requires_admin', {'return_value': False}),
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

    async def test_unconfirmed_stop_returns_failure(self):
        self.manager.stop.side_effect = RuntimeError('worker still alive')
        response = await routes_instances.stop(self.request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body)['message'], 'worker still alive')


class ControlTargetTests(unittest.TestCase):
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
