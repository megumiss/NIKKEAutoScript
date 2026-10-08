import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.device.win.app_control import AppControl


class WinStartupTests(unittest.TestCase):
    def test_auto_launcher_process_matches_renamed_executable(self):
        for auto_fill in (True, False):
            config = SimpleNamespace(
                PCClientInfo_LauncherPath=r'C:\NIKKE\Launcher\nikke_launcher2.exe',
                PCClientInfo_AutoFillName=auto_fill, PCClientInfo_Client='intl', PCClientInfo_GamePath='',
                PCClientInfo_LauncherProcessName='custom.exe', PCClientInfo_LauncherTitleName='',
                PCClientInfo_GameProcessName='', PCClientInfo_GameTitleName='',
                Vdd_VddScreen=False, PCClient_ScreenRotate=False, Client_Language='zh-CN',
            )
            with (
                self.subTest(auto_fill=auto_fill),
                patch('module.device.win.app_control.WinClient.__init__', return_value=None),
                patch.object(AppControl, 'check_path_format'),
                patch.object(AppControl, 'app_start', lambda self: setattr(self, 'current_window', self.game)),
                patch('module.device.win.app_control.Langs.use'),
                patch('module.device.win.app_control.set_server'),
                patch('module.device.win.app_control.set_language'),
            ):
                client = AppControl(config)
            expected = 'nikke_launcher2.exe' if auto_fill else 'custom.exe'
            self.assertEqual(client.launcher.process, expected)
            self.assertEqual(config.PCClientInfo_LauncherProcessName, expected)

    def test_driver_import_before_device_keeps_per_monitor_dpi(self):
        result = subprocess.run(
            [sys.executable, '-c', '''
import ctypes
from module.device.win.virtual_mouse.input import claim_scheme_mutex
from module.device.win.game_control import WinClient
awareness = ctypes.c_int()
assert ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness)) == 0
assert awareness.value == 2, f'Expected per-monitor DPI awareness, got {awareness.value}'
'''],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_resolution_failure_retries_game_instead_of_accepting_launcher(self):
        client = AppControl.__new__(AppControl)
        client.config = SimpleNamespace(
            PCClient_ScreenNumber=2, PCClient_GameWindowPosition='center',
            PCClient_CloseAutoHdr=True, PCClient_DisableVoice=False,
        )
        client.game = SimpleNamespace(name='Game')
        client.launcher = SimpleNamespace(name='Launcher')
        client.check_screen_resolution = Mock()
        client.change_auto_hdr = Mock()
        game_running = True
        resized = []

        def stop_program():
            nonlocal game_running
            if client.current_window is client.game:
                game_running = False

        def login():
            nonlocal game_running
            game_running = True
            client.current_window = client.game
            client.launcher_running = True

        client.stop_program = Mock(side_effect=stop_program)
        client.switch_to_program = Mock(
            side_effect=lambda: game_running if client.current_window is client.game else True,
        )
        client.login = Mock(side_effect=login)
        client.ensure_resolution = Mock(side_effect=lambda *args: resized.append(client.current_window.name))
        client.check_resolution = Mock(side_effect=[RuntimeError('Window resolution error'), None])

        with patch('module.device.win.app_control.time.sleep'):
            client.app_start()

        self.assertEqual(resized, ['Game', 'Game'])
        self.assertIs(client.current_window, client.game)
        client.login.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
