import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.device.win.app_control import AppControl
from module.exception import RequestHumanTakeover
from module.webui.process_manager import ProcessManager


class VddLifecycleTests(unittest.TestCase):
    def test_missing_vdd_does_not_start_game_or_rotate_configured_physical_screen(self):
        for auto_manage in (False, True):
            config = SimpleNamespace(
                PCClientInfo_LauncherPath=r'C:\NIKKE\launcher.exe',
                PCClientInfo_AutoFillName=True, PCClientInfo_Client='intl', PCClientInfo_GamePath='',
                Vdd_VddScreen=True, Vdd_VddAutoManage=auto_manage,
                PCClient_ScreenNumber=0, PCClient_ScreenRotate=True,
            )
            with (
                self.subTest(auto_manage=auto_manage),
                patch('module.device.win.app_control.WinClient.__init__', return_value=None),
                patch.object(AppControl, 'check_path_format'),
                patch('module.device.win.app_control.Window', side_effect=lambda **kwargs: SimpleNamespace(**kwargs)),
                patch('module.device.win.app_control.vdd_auto_start', return_value=None),
                patch('module.device.win.app_control.vdd_find_screen_n', return_value=None),
                patch('module.device.win.app_control.vdd_auto_stop') as stop,
                patch.object(AppControl, 'screen_rotate') as rotate,
                patch.object(AppControl, 'app_start') as start,
                self.assertRaises(RequestHumanTakeover),
            ):
                AppControl(config)
            rotate.assert_not_called()
            start.assert_not_called()
            self.assertEqual(stop.call_count, int(auto_manage))

    def test_stop_cleanup_skips_rotation_if_vdd_cannot_be_resolved(self):
        config = SimpleNamespace(
            Client_Platform='win', PCClient_ScreenRotate=True, PCClient_ScreenNumber=0,
            Vdd_VddScreen=True, Vdd_VddAutoManage=True,
        )
        manager = ProcessManager.__new__(ProcessManager)
        manager.config_name = 'test'
        for error in (None, RuntimeError('display is cloned')):
            with (
                self.subTest(error=error),
                patch.object(manager, '_restore_physical_device_resolution'),
                patch.object(manager, '_cleanup_virtual_display_server'),
                patch('module.config.config.NikkeConfig', return_value=config),
                patch('module.device.win.vdd.vdd_find_screen_n', return_value=None, side_effect=error),
                patch('module.device.win.vdd.vdd_auto_stop') as stop,
                patch('module.device.win.game_control.WinClient.screen_rotate') as rotate,
            ):
                manager._run_stop_cleanup()
            rotate.assert_not_called()
            stop.assert_called_once_with(config)


if __name__ == '__main__':
    unittest.main()
