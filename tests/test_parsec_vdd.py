import ctypes
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from module.device.win import display_config, parsec_vdd

PARSEC_TARGET = r'\\?\DISPLAY#PSCCDD0#VIRTUAL'
PHYSICAL_TARGET = r'\\?\DISPLAY#PHYSICAL#SECONDARY'


def display(target, source, position, device, index=0, size=(1920, 1080)):
    return {
        'index': index, 'target': target, 'source': source, 'position': position,
        'device': device, 'size': size, 'rotation': 1, 'refresh': (60, 1),
    }


def clone_displays():
    return [
        display(r'\\?\DISPLAY#PRIMARY', (0, 1, 0), (0, 0), r'\\.\DISPLAY1', size=(3840, 2160)),
        display(PHYSICAL_TARGET, (0, 1, 1), (3840, 0), r'\\.\DISPLAY2', index=1),
        display(PARSEC_TARGET, (0, 1, 1), (3840, 0), r'\\.\DISPLAY2', index=2),
    ]


def extended_displays():
    displays = clone_displays()
    displays[-1].update(source=(0, 2, 0), device=r'\\.\DISPLAY10', position=(5760, 0))
    return displays


@unittest.skipUnless(os.name == 'nt', 'Windows display configuration ABI')
class DisplayConfigTests(unittest.TestCase):
    def setUp(self):
        self.before = clone_displays()
        self.aware = clone_displays()
        self.aware[-1].update(source=(0, 2, 0), device=r'\\.\DISPLAY10')
        self.paths = (display_config.DisplayPath * 3)()
        self.modes = (display_config.ModeInfo * 3)()
        for index, item in enumerate(self.aware):
            path = self.paths[index]
            path.flags = 9
            path.source.adapter.low = item['source'][1]
            path.source.id = item['source'][2]
            path.source.mode_index = (index << 16) | 0xffff
            mode = self.modes[index]
            mode.type = display_config.DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE
            mode.source.width, mode.source.height = item['size']
            mode.source.position.x, mode.source.position.y = item['position']

    def test_windows_structure_sizes(self):
        self.assertEqual(ctypes.sizeof(display_config.DisplayPath), 72)
        self.assertEqual(ctypes.sizeof(display_config.ModeInfo), 64)
        self.assertEqual(ctypes.sizeof(display_config.SourceDeviceName), 84)
        self.assertEqual(ctypes.sizeof(display_config.TargetDeviceName), 420)

    def test_detach_changes_only_virtual_desktop_position(self):
        physical_modes = bytes(self.modes[0]), bytes(self.modes[1])
        original_paths = bytes(self.paths)
        with (
            patch.object(display_config, 'active_displays', side_effect=[self.before, extended_displays()]),
            patch.object(display_config, '_query', return_value=(self.paths, self.modes)),
            patch.object(display_config, '_describe', return_value=self.aware),
            patch.object(display_config, '_apply') as apply,
        ):
            display_config.extend_display(PARSEC_TARGET)
        self.assertEqual((self.modes[2].source.position.x, self.modes[2].source.position.y), (5760, 0))
        self.assertEqual((bytes(self.modes[0]), bytes(self.modes[1])), physical_modes)
        self.assertEqual(bytes(self.paths), original_paths)
        self.assertEqual(apply.call_count, 2)
        self.assertEqual(apply.call_args_list[0].kwargs, {'validate': True})

    def test_extended_display_does_not_change_configuration(self):
        with (
            patch.object(display_config, 'active_displays', return_value=extended_displays()),
            patch.object(display_config, '_query') as query,
            patch.object(display_config, '_apply') as apply,
        ):
            display_config.extend_display(PARSEC_TARGET)
        query.assert_not_called()
        apply.assert_not_called()

    def test_shared_native_source_is_not_moved(self):
        self.aware[-1]['source'] = self.aware[1]['source']
        self.paths[2].source.adapter.low = self.paths[1].source.adapter.low
        self.paths[2].source.id = self.paths[1].source.id
        with (
            patch.object(display_config, 'active_displays', return_value=self.before),
            patch.object(display_config, '_query', return_value=(self.paths, self.modes)),
            patch.object(display_config, '_describe', return_value=self.aware),
            patch.object(display_config, '_apply') as apply,
            self.assertRaisesRegex(OSError, 'Cannot safely detach'),
        ):
            display_config.extend_display(PARSEC_TARGET)
        apply.assert_not_called()

    def test_failed_verification_restores_original_modes(self):
        for failure in ('still_cloned', 'physical_changed'):
            with self.subTest(failure=failure):
                self.setUp()
                original_modes = bytes(self.modes)
                after = clone_displays() if failure == 'still_cloned' else extended_displays()
                if failure == 'physical_changed':
                    after[1]['rotation'] = 2
                with (
                    patch.object(display_config, 'active_displays', side_effect=[self.before, after]),
                    patch.object(display_config, '_query', return_value=(self.paths, self.modes)),
                    patch.object(display_config, '_describe', return_value=self.aware),
                    patch.object(display_config, '_apply') as apply,
                    self.assertRaises(OSError),
                ):
                    display_config.extend_display(PARSEC_TARGET)
                self.assertEqual(apply.call_count, 3)
                self.assertEqual(bytes(apply.call_args.args[1]), original_modes)

    def test_validation_failure_does_not_apply(self):
        with (
            patch.object(display_config, 'active_displays', return_value=self.before),
            patch.object(display_config, '_query', return_value=(self.paths, self.modes)),
            patch.object(display_config, '_describe', return_value=self.aware),
            patch.object(display_config, '_apply', side_effect=OSError(87, 'invalid')) as apply,
            self.assertRaises(OSError),
        ):
            display_config.extend_display(PARSEC_TARGET)
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(apply.call_args.kwargs, {'validate': True})

    def test_query_retries_topology_growth_and_trims_shrunk_buffers(self):
        api = Mock()

        def sizes(flags, paths, modes):
            paths._obj.value, modes._obj.value = 3, 3
            return 0

        def query(flags, path_count, paths, mode_count, modes, topology):
            if api.QueryDisplayConfig.call_count == 1:
                return display_config.ERROR_INSUFFICIENT_BUFFER
            path_count._obj.value, mode_count._obj.value = 1, 2
            return 0

        api.GetDisplayConfigBufferSizes.side_effect = sizes
        api.QueryDisplayConfig.side_effect = query
        with patch.object(display_config, '_user32', return_value=api):
            paths, modes = display_config._query()
        self.assertEqual((len(paths), len(modes)), (1, 2))
        self.assertEqual(api.QueryDisplayConfig.call_count, 2)


@unittest.skipUnless(os.name == 'nt', 'Windows Parsec display management')
class ParsecVddTests(unittest.TestCase):
    def test_snapshot_restore_setting_survives_repeated_takeover_and_stop(self):
        import winreg

        for original in (None, (0, winreg.REG_DWORD), (1, winreg.REG_DWORD)):
            with self.subTest(original=original):
                values = {} if original is None else {'RestoreDisplays': original}
                key = MagicMock()

                def query(key, name):
                    if name not in values:
                        raise FileNotFoundError(name)
                    return values[name]

                def set_value(key, name, reserved, kind, value):
                    values[name] = (value, kind)

                def delete_value(key, name):
                    if name not in values:
                        raise FileNotFoundError(name)
                    del values[name]

                with (
                    patch.object(winreg, 'CreateKeyEx', return_value=key),
                    patch.object(winreg, 'OpenKey', return_value=key),
                    patch.object(winreg, 'QueryValueEx', side_effect=query),
                    patch.object(winreg, 'SetValueEx', side_effect=set_value),
                    patch.object(winreg, 'DeleteValue', side_effect=delete_value),
                ):
                    parsec_vdd._disable_snapshot_restore()
                    self.assertEqual(values['RestoreDisplays'], (0, winreg.REG_DWORD))
                    parsec_vdd._disable_snapshot_restore()
                    parsec_vdd._restore_snapshot_restore()
                self.assertEqual(values, {} if original is None else {'RestoreDisplays': original})

    def test_process_start_disables_snapshot_restore_before_launch(self):
        order = []
        process = SimpleNamespace(_handle=123)
        with (
            patch.object(parsec_vdd, 'is_app_running', return_value=False),
            patch.object(parsec_vdd.os.path, 'isfile', return_value=True),
            patch.object(parsec_vdd, '_disable_snapshot_restore', side_effect=lambda: order.append('disable')),
            patch.object(parsec_vdd.subprocess, 'Popen', side_effect=lambda *a, **kw: order.append('start') or process),
            patch.object(parsec_vdd, '_wait_app_ready', return_value=True),
        ):
            parsec_vdd.start_app()
        self.assertEqual(order, ['disable', 'start'])

    def test_launch_failure_restores_snapshot_preference(self):
        with (
            patch.object(parsec_vdd, 'is_app_running', return_value=False),
            patch.object(parsec_vdd.os.path, 'isfile', return_value=True),
            patch.object(parsec_vdd, '_disable_snapshot_restore'),
            patch.object(parsec_vdd.subprocess, 'Popen', side_effect=OSError('launch failed')),
            patch.object(parsec_vdd, '_restore_snapshot_restore') as restore,
            self.assertRaises(parsec_vdd.ParsecVddError),
        ):
            parsec_vdd.start_app()
        restore.assert_called_once()

    def test_stop_restores_snapshot_preference_after_process_disappeared(self):
        with (
            patch.object(parsec_vdd, '_iter_app_processes', return_value=[]),
            patch.object(parsec_vdd, '_restore_snapshot_restore') as restore,
        ):
            parsec_vdd.stop_app()
        restore.assert_called_once()

    def test_app_ready_waits_for_a_window_owned_by_the_started_process(self):
        process = SimpleNamespace(pid=123, poll=Mock(return_value=None))
        with (
            patch('win32gui.EnumWindows') as enum,
            patch('win32process.GetWindowThreadProcessId', return_value=(1, 123)),
            patch.object(parsec_vdd.time, 'sleep'),
            patch.object(parsec_vdd.time, 'monotonic', side_effect=[0, 0, 1, 11]),
        ):
            enum.side_effect = lambda visit, _: visit(10, None) if enum.call_count == 2 else None
            self.assertTrue(parsec_vdd._wait_app_ready(process))
        self.assertEqual(enum.call_count, 2)

    def test_app_ready_stops_waiting_when_process_exits(self):
        process = SimpleNamespace(pid=123, poll=Mock(return_value=1))
        with patch('win32gui.EnumWindows') as enum:
            self.assertFalse(parsec_vdd._wait_app_ready(process))
        enum.assert_not_called()

    def test_wait_detects_cloned_target_without_monitor_count_increase(self):
        with (
            patch.object(parsec_vdd, '_active_displays', return_value=clone_displays()),
            patch('win32api.EnumDisplayMonitors', side_effect=AssertionError('Do not infer creation from count')),
            patch.object(parsec_vdd.time, 'sleep') as sleep,
        ):
            displays = parsec_vdd._wait_parsec_displays()
        self.assertEqual(displays[0]['target'], PARSEC_TARGET)
        sleep.assert_not_called()

    def test_wait_has_bounded_timeout_for_absent_target(self):
        with (
            patch.object(parsec_vdd, '_active_displays', return_value=[]),
            patch.object(parsec_vdd.time, 'monotonic', side_effect=[0, 0, 60]),
            patch.object(parsec_vdd.time, 'sleep'),
        ):
            self.assertEqual(parsec_vdd._wait_parsec_displays(), [])

    def test_start_detaches_before_rotation_and_does_not_add_another_clone(self):
        order = []
        with (
            patch.object(parsec_vdd, 'driver_status', return_value={'installed': True, 'version': '0.45'}),
            patch.object(parsec_vdd, 'is_app_running', return_value=False),
            patch.object(parsec_vdd, 'start_app'),
            patch.object(parsec_vdd, '_active_displays', return_value=clone_displays()),
            patch.object(display_config, 'extend_display', side_effect=lambda _: order.append('extend')),
            patch.object(parsec_vdd, '_set_mode_1080p_portrait', side_effect=lambda _: order.append('rotate') or True),
            patch.object(parsec_vdd, 'find_screen_n', return_value=2) as find,
            patch.object(parsec_vdd, '_cli') as cli,
        ):
            self.assertEqual(parsec_vdd.ensure_screen_1080p_portrait(), 2)
        self.assertEqual(order, ['extend', 'rotate'])
        find.assert_called_once_with(PARSEC_TARGET)
        cli.assert_not_called()

    def test_detach_failure_stops_owned_app_without_rotation(self):
        with (
            patch.object(parsec_vdd, 'driver_status', return_value={'installed': True, 'version': '0.45'}),
            patch.object(parsec_vdd, 'is_app_running', return_value=False),
            patch.object(parsec_vdd, 'start_app'),
            patch.object(parsec_vdd, '_active_displays', return_value=clone_displays()),
            patch.object(display_config, 'extend_display', side_effect=OSError('rejected')),
            patch.object(parsec_vdd, '_set_mode_1080p_portrait') as rotate,
            patch.object(parsec_vdd, 'stop_app') as stop,
            self.assertRaises(parsec_vdd.ParsecVddError),
        ):
            parsec_vdd.ensure_screen_1080p_portrait()
        rotate.assert_not_called()
        stop.assert_called_once()

    def test_mode_or_screen_lookup_failure_does_not_fall_back(self):
        for mode_ok, screen in ((False, 2), (True, None)):
            with (
                self.subTest(mode_ok=mode_ok, screen=screen),
                patch.object(parsec_vdd, 'driver_status', return_value={'installed': True, 'version': '0.45'}),
                patch.object(parsec_vdd, 'is_app_running', return_value=True),
                patch.object(parsec_vdd, '_active_displays', return_value=extended_displays()),
                patch.object(display_config, 'extend_display'),
                patch.object(parsec_vdd, '_set_mode_1080p_portrait', return_value=mode_ok),
                patch.object(parsec_vdd, 'find_screen_n', return_value=screen),
                patch.object(parsec_vdd, 'stop_app') as stop,
                self.assertRaises(parsec_vdd.ParsecVddError),
            ):
                parsec_vdd.ensure_screen_1080p_portrait()
            stop.assert_not_called()

    def test_rotation_refuses_shared_source_even_if_device_name_looks_valid(self):
        with (
            patch.object(parsec_vdd, '_active_displays', return_value=clone_displays()),
            patch('win32api.ChangeDisplaySettingsEx') as change,
            self.assertRaises(parsec_vdd.ParsecVddError),
        ):
            parsec_vdd._set_mode_1080p_portrait(PARSEC_TARGET)
        change.assert_not_called()

    def test_mode_readback_requires_portrait_orientation(self):
        current = SimpleNamespace(PelsWidth=1920, PelsHeight=1080, DisplayOrientation=0, DisplayFrequency=60)
        applied = SimpleNamespace(PelsWidth=1080, PelsHeight=1920, DisplayOrientation=0, DisplayFrequency=60)
        with (
            patch.object(parsec_vdd, '_active_displays', return_value=extended_displays()),
            patch('win32api.EnumDisplaySettings', side_effect=[current, applied]),
            patch('win32api.ChangeDisplaySettingsEx', return_value=0),
        ):
            self.assertFalse(parsec_vdd._set_mode_1080p_portrait(PARSEC_TARGET))

    def test_screen_lookup_uses_target_identity_and_independent_device(self):
        with (
            patch.object(parsec_vdd, '_active_displays', return_value=extended_displays()),
            patch('win32api.EnumDisplayMonitors', return_value=[(10,), (20,)]),
            patch('win32api.GetMonitorInfo', side_effect=[{'Device': r'\\.\DISPLAY1'}, {'Device': r'\\.\DISPLAY10'}]),
            patch.object(parsec_vdd, '_cli') as cli,
        ):
            self.assertEqual(parsec_vdd.find_screen_n(PARSEC_TARGET), 1)
        cli.assert_not_called()

    def test_manual_lookup_rejects_clone(self):
        with (
            patch.object(parsec_vdd, '_active_displays', return_value=clone_displays()),
            self.assertRaises(parsec_vdd.ParsecVddError),
        ):
            parsec_vdd.find_screen_n()


if __name__ == '__main__':
    unittest.main()
