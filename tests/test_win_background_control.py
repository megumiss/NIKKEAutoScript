import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.device.win.app_control import AppControl
from module.device.win.automation import Automation
from module.device.win.input import Input
from module.device.win.ok_interaction.hwnd_window import HwndWindowAdapter
from module.device.win.ok_interaction.input import PostMessageInput
from module.device.win.ok_interaction.post_message import PostMessageInteraction
from module.device.win.virtual_mouse.driver_mouse import BTN_LEFT, VirtualMouse, VirtualMouseDevice, make_report
from module.device.win.virtual_mouse.input import FAILURE_LIMIT, VirtualMouseInput
from module.exception import RequestHumanTakeover
from module.tools import virtual_mouse_driver

_DEVICE_PATH = r'\\?\root#system#0002#{1abc05c0-c378-41b9-9cef-df1aba82b015}'


def _client(window_name):
    client = AppControl.__new__(AppControl)
    client.config = SimpleNamespace(PCClientInfo_ControlScheme='postmessage')
    client.current_window = SimpleNamespace(name=window_name)
    return client


def _input(window_name='Game'):
    window = SimpleNamespace(name=window_name, title='NIKKE')
    with patch.object(Input, '__init__', return_value=None):
        handler = PostMessageInput(lambda: window, hwnd_resolver=lambda: 0)
    return handler


class BackgroundControlTests(unittest.TestCase):
    def test_automation_background_scroll_uses_inertia_free_scroll(self):
        automation = Automation.__new__(Automation)
        automation.config = SimpleNamespace(PCClientInfo_ControlScheme='postmessage')
        automation.current_window = SimpleNamespace(offset=(100, 200))
        automation.mouse_swipe = Mock()
        automation.mouse_move = Mock()
        automation.mouse_scroll = Mock()

        automation.swipe((10, 20), (10, 320), speed=7, method='scroll')

        automation.mouse_swipe.assert_not_called()
        automation.mouse_move.assert_called_once_with(110, 370)
        automation.mouse_scroll.assert_called_once_with(4, direction=1)

    def test_automation_foreground_scroll_uses_wheel(self):
        automation = Automation.__new__(Automation)
        automation.config = SimpleNamespace(PCClientInfo_ControlScheme='pyautogui')
        automation.current_window = SimpleNamespace(offset=(100, 200))
        automation.mouse_swipe = Mock()
        automation.mouse_move = Mock()
        automation.mouse_scroll = Mock()

        automation.swipe((10, 20), (10, 320), speed=7, method='scroll')

        automation.mouse_swipe.assert_not_called()
        automation.mouse_move.assert_called_once_with((110 + 110) // 2, (220 + 520) // 2)
        automation.mouse_scroll.assert_called_once_with(4, direction=1)

    def test_automation_short_scroll_still_scrolls_once(self):
        for scheme in ('postmessage', 'pyautogui'):
            with self.subTest(scheme=scheme):
                automation = Automation.__new__(Automation)
                automation.config = SimpleNamespace(PCClientInfo_ControlScheme=scheme)
                automation.current_window = SimpleNamespace(offset=(100, 200))
                automation.mouse_move = Mock()
                automation.mouse_scroll = Mock()

                # 60px 短距离：round(60/65)-1 = 0，必须兜底为至少一次滚动
                automation.swipe((10, 20), (10, 80), speed=7, method='scroll')

                automation.mouse_scroll.assert_called_once_with(1, direction=1)

    def test_automation_zero_distance_scroll_does_nothing(self):
        for scheme in ('postmessage', 'pyautogui'):
            with self.subTest(scheme=scheme):
                automation = Automation.__new__(Automation)
                automation.config = SimpleNamespace(PCClientInfo_ControlScheme=scheme)
                automation.current_window = SimpleNamespace(offset=(100, 200))
                automation.mouse_move = Mock()
                automation.mouse_scroll = Mock()

                automation.swipe((10, 20), (10, 20), speed=7, method='scroll')

                automation.mouse_scroll.assert_called_once_with(0, direction=1)

    def test_child_window_geometry_matches_interaction_layout(self):
        def enum_children(_hwnd, callback, context):
            callback(20, context)

        with (
            patch('module.device.win.ok_interaction.hwnd_window.win32gui.EnumChildWindows', side_effect=enum_children),
            patch('module.device.win.ok_interaction.hwnd_window.win32gui.IsWindowVisible', return_value=True),
            patch('module.device.win.ok_interaction.hwnd_window.win32gui.GetWindowRect', return_value=(100, 200, 400, 600)),
        ):
            self.assertEqual(HwndWindowAdapter._enum_hwnds(10), [[20, '', 300, 400, 100, 200]])

    def test_postmessage_uses_resolved_window_handle(self):
        window = SimpleNamespace(hwnd=42, title='NIKKE', class_name='UnityWndClass', name='Game')
        adapter = HwndWindowAdapter(lambda: window, hwnd_resolver=lambda: 42)
        adapter._enum_hwnds = Mock(return_value=[])

        with (
            patch('module.device.win.ok_interaction.hwnd_window.win32gui.IsWindow', return_value=True),
            patch('module.device.win.ok_interaction.hwnd_window.win32gui.GetWindowText', return_value='NIKKE'),
            patch('module.device.win.ok_interaction.hwnd_window.win32gui.GetClassName', return_value='UnityWndClass'),
            patch('module.device.win.ok_interaction.hwnd_window.win32gui.FindWindow') as find_window,
        ):
            self.assertTrue(adapter.update())

        self.assertEqual(adapter.hwnd, 42)
        find_window.assert_not_called()

    def test_background_control_only_applies_to_game(self):
        self.assertTrue(_client('Game')._background_control)
        self.assertFalse(_client('Launcher')._background_control)

    def test_background_running_check_uses_exact_window_lookup(self):
        client = _client('Game')
        client.find_program_window = Mock(return_value=123)
        client.check_program = Mock(return_value=True)

        self.assertTrue(client.app_is_running())
        client.find_program_window.assert_called_once_with()
        client.check_program.assert_not_called()

    def test_launcher_click_falls_back_to_foreground_input(self):
        handler = _input('Launcher')

        with patch.object(Input, 'mouse_click') as mouse_click:
            handler.mouse_click(10, 20)

        mouse_click.assert_called_once_with(10, 20)

    def test_launcher_keyboard_falls_back_to_foreground_input(self):
        handler = _input('Launcher')

        with patch.object(Input, 'secretly_press_key') as press_key:
            handler.secretly_press_key('a', wait_time=0.1)

        press_key.assert_called_once_with('a', wait_time=0.1)

    def test_switch_only_leaves_game_in_background(self):
        game = _client('Game')
        game.current_window.title = 'NIKKE'
        game.find_program_window = Mock(return_value=10)
        game.set_foreground_window_with_retry = Mock()

        launcher = _client('Launcher')
        launcher.current_window.title = 'NIKKE'
        launcher.find_program_window = Mock(return_value=20)
        launcher.set_foreground_window_with_retry = Mock()

        self.assertTrue(game.switch_to_program())
        self.assertTrue(launcher.switch_to_program())
        game.set_foreground_window_with_retry.assert_not_called()
        launcher.set_foreground_window_with_retry.assert_called_once_with(20)

    def test_background_mouse_move_does_not_move_real_cursor(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=True)
        handler._to_client = Mock(return_value=(5, 6))
        handler.interaction = Mock()

        with patch('module.device.win.ok_interaction.input.win32api.SetCursorPos') as set_cursor:
            handler.mouse_move(100, 200)

        set_cursor.assert_not_called()
        self.assertEqual(handler._mouse_screen_position, (100, 200))
        handler.interaction.move.assert_called_once_with(5, 6)

    def test_scroll_uses_cached_position_and_postmessage_swipe(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=True)
        handler.interaction = Mock()
        handler._mouse_screen_position = (100, 200)
        handler._postmessage_swipe = Mock()

        with patch('module.device.win.ok_interaction.input.time.sleep') as sleep:
            handler._scroll(2, -1)

        self.assertEqual(
            handler._postmessage_swipe.call_args_list,
            [
                call((100, 200), (100, 135), 0.2, release_delay=0.12),
                call((100, 200), (100, 135), 0.2, release_delay=0.12),
            ],
        )
        sleep.assert_any_call(0.1)

    def test_scroll_blocks_input_and_restores_cursor(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=True)
        handler.interaction = Mock()
        handler._mouse_screen_position = (100, 200)
        handler._block_input = Mock()
        handler._unblock_input = Mock()
        handler._postmessage_swipe = Mock()

        with (
            patch('module.device.win.ok_interaction.input.win32api.GetCursorPos', return_value=(300, 400)),
            patch('module.device.win.ok_interaction.input.win32api.SetCursorPos') as set_cursor,
            patch('module.device.win.ok_interaction.input.time.sleep'),
        ):
            handler.mouse_scroll(2, direction=-1)

        handler._block_input.assert_called_once_with()
        handler._unblock_input.assert_called_once_with()
        self.assertEqual(set_cursor.call_args_list[-1].args, ((300, 400),))

    def test_inertia_free_scroll_waits_at_endpoint_before_release(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=True)
        handler.interaction = Mock()
        handler.interaction.hwnd = 42
        handler._to_client = Mock(return_value=(10, 20))
        handler.interaction.update_mouse_pos.side_effect = [1001, 1002, 1003, 1004, 1005, 1006, 1007]
        events = Mock()
        events.attach_mock(handler.interaction.post, 'post')

        with (
            patch('module.device.win.ok_interaction.input.win32api.SetCursorPos'),
            patch('module.device.win.ok_interaction.input.time.sleep') as sleep,
        ):
            events.attach_mock(sleep, 'sleep')
            handler._postmessage_swipe((10, 20), (30, 40), 0.15, release_delay=0.12)

        self.assertEqual(events.mock_calls[-2], call.sleep(0.12))
        self.assertEqual(events.mock_calls[-1], call.post(0x0202, 0, 1007, hwnd=42))

    def test_swipe_does_not_change_foreground_window(self):
        handler = _input()
        handler._operate = lambda fun, **_: fun()
        handler._ensure_window = Mock(return_value=True)
        handler.interaction = Mock()
        handler.interaction.hwnd = 42
        handler._to_client = Mock(return_value=(10, 20))
        handler.interaction.update_mouse_pos.side_effect = [1001, 1002, 1003, 1004, 1005, 1006, 1007]

        with (
            patch('module.device.win.ok_interaction.input.win32api.SetCursorPos') as set_cursor,
            patch('module.device.win.ok_interaction.input.time.sleep'),
            patch('module.device.win.ok_interaction.input.win32gui.SetForegroundWindow', create=True) as set_foreground,
        ):
            handler.mouse_swipe((10, 20), (30, 40), speed=5)

        set_foreground.assert_not_called()
        handler.interaction.try_activate.assert_called_once_with()
        self.assertEqual(set_cursor.call_args_list[0].args, ((10, 20),))
        self.assertEqual(set_cursor.call_args_list[-1].args, ((30, 40),))
        posted_messages = [call.args[:3] for call in handler.interaction.post.call_args_list]
        self.assertEqual(posted_messages[0], (0x0200, 0, 1001))
        self.assertEqual(posted_messages[1], (0x0201, 1, 1001))
        self.assertEqual(posted_messages[-1], (0x0202, 0, 1007))
        self.assertTrue(all(call.kwargs.get('hwnd') == 42 for call in handler.interaction.post.call_args_list))

    def test_swipe_blocks_input_and_restores_cursor(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=True)
        handler.interaction = Mock()
        handler.interaction.hwnd = 42
        handler._to_client = Mock(return_value=(10, 20))
        handler.interaction.update_mouse_pos.return_value = 1001
        handler._block_input = Mock()
        handler._unblock_input = Mock()

        with (
            patch('module.device.win.ok_interaction.input.win32api.GetCursorPos', return_value=(300, 400)),
            patch('module.device.win.ok_interaction.input.win32api.SetCursorPos') as set_cursor,
            patch('module.device.win.ok_interaction.input.time.sleep'),
        ):
            handler.mouse_swipe((10, 20), (30, 40), speed=5)

        handler._block_input.assert_called_once_with()
        handler._unblock_input.assert_called_once_with()
        self.assertEqual(set_cursor.call_args_list[-1].args, ((300, 400),))

    def test_background_keyboard_posts_key_messages_to_current_top_window(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=True)
        handler.hwnd_window.top_hwnd = 42
        handler._foreground_send_key = Mock(return_value=True)

        handler.press_key('a', wait_time=0.1)

        handler._foreground_send_key.assert_called_once_with(42, 'a', 0.1)

    def test_background_keyboard_reports_undelivered_key(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=True)
        handler.hwnd_window.top_hwnd = 42
        handler._foreground_send_key = Mock(return_value=False)

        with patch('module.device.win.ok_interaction.input.logger.warning') as warning:
            handler.press_key('a', wait_time=0.1)

        warning.assert_called_once_with('Foreground key press a was not delivered')

    def test_background_keyboard_returns_false_when_window_is_missing(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=False)

        self.assertFalse(handler.press_key('a', wait_time=0.1))

    def test_background_keyboard_returns_false_on_send_exception(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=True)
        handler.hwnd_window.top_hwnd = 42
        handler._foreground_send_key = Mock(side_effect=RuntimeError('send failed'))

        with patch('module.device.win.ok_interaction.input.logger.error'):
            self.assertFalse(handler.secretly_press_key('a', wait_time=0.1))

    def test_foreground_keyboard_restores_previous_window(self):
        handler = _input()
        handler.foreground_switcher = Mock()

        with (
            patch('module.device.win.ok_interaction.input.win32gui.GetForegroundWindow', side_effect=[100, 42]),
            patch('module.device.win.ok_interaction.input.win32gui.IsWindow', return_value=True),
            patch('module.device.win.ok_interaction.input.win32gui.SetForegroundWindow') as set_foreground,
            patch.object(Input, 'secretly_press_key') as send_input,
        ):
            self.assertTrue(handler._foreground_send_key(42, 'a', 0.1))

        handler.foreground_switcher.assert_called_once_with(42)
        send_input.assert_called_once_with(handler, 'a', wait_time=0.1)
        set_foreground.assert_called_once_with(100)

    def test_background_keyboard_holds_shift_for_shifted_character(self):
        handler = _input()
        handler._ensure_window = Mock(return_value=True)
        handler.hwnd_window.top_hwnd = 42
        handler._foreground_send_key = Mock(return_value=True)

        handler.secretly_press_key('!', wait_time=0.1)

        handler._foreground_send_key.assert_called_once_with(42, '!', 0.1)

    def test_key_lparam_contains_scan_code_and_key_up_bits(self):
        with patch('module.device.win.ok_interaction.post_message.win32api.MapVirtualKey', return_value=0x4D):
            self.assertEqual(PostMessageInteraction.make_key_lparam(0x4D), 0x4D0001)
            self.assertEqual(PostMessageInteraction.make_key_lparam(0x4D, key_up=True), 0xC04D0001)


class DriverSchemeTests(unittest.TestCase):
    def _handler(self, driver=None):
        with (
            patch.object(VirtualMouseInput, '_preflight', return_value=None),
            patch.object(Input, '__init__', return_value=None),
        ):
            handler = VirtualMouseInput(config_name='nkas')
        handler.mouse_driver = driver or Mock()
        return handler

    def test_automation_selects_logi_input_for_driver_scheme(self):
        automation = Automation.__new__(Automation)
        automation.config = SimpleNamespace(PCClientInfo_ControlScheme='driver', config_name='nkas')
        with patch('module.device.win.virtual_mouse.input.VirtualMouseInput') as logi:
            automation._init_input()
        logi.assert_called_once_with(config_name='nkas')

    def test_automation_unknown_scheme_falls_back_to_plain_input(self):
        automation = Automation.__new__(Automation)
        automation.config = SimpleNamespace(PCClientInfo_ControlScheme='whatever', config_name='nkas')
        with patch('module.device.win.automation.Input') as plain:
            automation._init_input()
        plain.assert_called_once_with()

    def test_driver_scheme_is_foreground_not_background(self):
        client = AppControl.__new__(AppControl)
        client.config = SimpleNamespace(PCClientInfo_ControlScheme='driver')
        client.current_window = SimpleNamespace(name='Game')
        self.assertFalse(client._background_control)

    def test_preflight_stops_when_device_is_missing(self):
        with (
            patch('module.device.win.virtual_mouse.input.claim_scheme_mutex', return_value=True),
            patch.object(VirtualMouseDevice, 'open', return_value=False),
            patch('module.device.win.virtual_mouse.input.driver_package_present', return_value=False),
            patch('module.device.win.virtual_mouse.input.sub_device_present', return_value=None),
            patch.object(Input, '__init__', return_value=None),
            patch('module.device.win.virtual_mouse.input.logger.error'),
        ):
            with self.assertRaises(RequestHumanTakeover):
                VirtualMouseInput(config_name='nkas')

    def test_preflight_repairs_hidden_device_and_retries_open(self):
        with (
            patch('module.device.win.virtual_mouse.input.claim_scheme_mutex', return_value=True),
            patch.object(VirtualMouseDevice, 'open', side_effect=[False, True]),
            patch('module.device.win.virtual_mouse.input.sub_device_present', return_value=True),
            patch('module.device.win.virtual_mouse.input.driver_package_present', return_value=True),
            patch('module.device.win.virtual_mouse.input.repair_driver', return_value=True) as repair,
            patch.object(Input, '__init__', return_value=None),
        ):
            VirtualMouseInput(config_name='nkas')
        repair.assert_called_once_with()

    def test_preflight_repairs_when_hid_sub_device_is_phantom(self):
        # 接口能打开但子设备是幽灵设备（代码 45）：只有补上子设备判据才会触发修复
        with (
            patch('module.device.win.virtual_mouse.input.claim_scheme_mutex', return_value=True),
            patch.object(VirtualMouseDevice, 'open', return_value=True),
            patch('module.device.win.virtual_mouse.input.sub_device_present',
                  side_effect=[False, True]) as sub_device,
            patch('module.device.win.virtual_mouse.input.driver_package_present', return_value=True),
            patch('module.device.win.virtual_mouse.input.repair_driver', return_value=True) as repair,
            patch.object(Input, '__init__', return_value=None),
        ):
            VirtualMouseInput(config_name='nkas')
        repair.assert_called_once_with()
        self.assertEqual(sub_device.call_count, 2)

    def test_preflight_stops_when_hid_sub_device_stays_phantom(self):
        with (
            patch('module.device.win.virtual_mouse.input.claim_scheme_mutex', return_value=True),
            patch.object(VirtualMouseDevice, 'open', return_value=True),
            patch('module.device.win.virtual_mouse.input.sub_device_present', return_value=False),
            patch('module.device.win.virtual_mouse.input.driver_package_present', return_value=True),
            patch('module.device.win.virtual_mouse.input.repair_driver', return_value=True),
            patch.object(Input, '__init__', return_value=None),
            patch('module.device.win.virtual_mouse.input.logger.error') as logged,
        ):
            with self.assertRaises(RequestHumanTakeover):
                VirtualMouseInput(config_name='nkas')
        self.assertIn('code 45', logged.call_args[0][0])

    def test_preflight_stops_when_repair_fails(self):
        for repaired in (False, True):
            with self.subTest(repaired=repaired):
                with (
                    patch('module.device.win.virtual_mouse.input.claim_scheme_mutex', return_value=True),
                    patch.object(VirtualMouseDevice, 'open', return_value=False),
                    patch('module.device.win.virtual_mouse.input.driver_package_present', return_value=repaired),
                    patch('module.device.win.virtual_mouse.input.sub_device_present', return_value=None),
                    patch('module.device.win.virtual_mouse.input.repair_driver', return_value=False),
                    patch.object(Input, '__init__', return_value=None),
                    patch('module.device.win.virtual_mouse.input.logger.error'),
                ):
                    with self.assertRaises(RequestHumanTakeover):
                        VirtualMouseInput(config_name='nkas')

    def test_preflight_stops_when_another_instance_holds_the_scheme(self):
        with (
            patch('module.device.win.virtual_mouse.input.claim_scheme_mutex', return_value=False),
            patch.object(VirtualMouseDevice, 'open', return_value=True) as opened,
            patch.object(Input, '__init__', return_value=None),
            patch('module.device.win.virtual_mouse.input.logger.error'),
        ):
            with self.assertRaises(RequestHumanTakeover):
                VirtualMouseInput(config_name='nkas')
        opened.assert_not_called()

    def test_mouse_click_moves_then_presses_then_releases(self):
        driver = Mock()
        handler = self._handler(driver)
        with patch('module.device.win.virtual_mouse.input.time.sleep'):
            handler.mouse_click(120, 340)
        self.assertEqual(
            driver.mock_calls,
            [call.move_to(120, 340, buttons=0), call.press(BTN_LEFT), call.release()],
        )

    def test_mouse_scroll_maps_direction_to_signed_notches(self):
        driver = Mock()
        handler = self._handler(driver)
        handler.mouse_scroll(3, direction=-1)
        handler.mouse_scroll(2, direction=1)
        handler.mouse_scroll(0)
        self.assertEqual(driver.wheel.call_args_list, [call(-3), call(2)])

    def test_swipe_holds_left_button_on_every_waypoint(self):
        driver = Mock()
        handler = self._handler(driver)
        with patch('module.device.win.virtual_mouse.input.time.sleep'):
            handler.mouse_swipe((100, 100), (100, 350), speed=5)
        calls = driver.move_to.call_args_list
        self.assertEqual(calls[0], call(100, 100, buttons=0))
        self.assertTrue(all(item.kwargs['buttons'] == BTN_LEFT for item in calls[1:]))
        self.assertGreater(len(calls), 2)
        self.assertEqual(driver.press.call_args_list, [call(BTN_LEFT)])
        self.assertEqual(driver.release.call_args_list, [call()])

    def test_failure_limit_raises_instead_of_falling_back(self):
        driver = Mock()
        driver.move_to.return_value = False
        handler = self._handler(driver)
        with (
            patch('module.device.win.virtual_mouse.input.logger.error'),
            patch('module.device.win.virtual_mouse.input.logger.critical'),
        ):
            with self.assertRaises(RequestHumanTakeover):
                for _ in range(FAILURE_LIMIT):
                    handler.mouse_move(10, 10)

    def test_report_layout_is_seven_bytes_little_endian(self):
        self.assertEqual(make_report(buttons=1, dx=0, dy=0, wheel=0).hex(), '01000000000000')
        self.assertEqual(make_report(buttons=1, dx=-2, dy=258, wheel=-1).hex(), '0100feff0201ff')
        self.assertEqual(make_report(dx=40000).hex(), '0000ff7f000000')

    def test_failed_positioning_never_presses_at_the_old_position(self):
        for method in ('mouse_click', 'press_mouse_click', 'mouse_down'):
            with self.subTest(method=method):
                driver = Mock()
                driver.move_to.return_value = False
                handler = self._handler(driver)
                with patch('module.device.win.virtual_mouse.input.time.sleep'):
                    getattr(handler, method)(120, 340)
                driver.press.assert_not_called()
                self.assertEqual(handler._failures, 1)

    def test_repeated_button_failures_are_not_reset_by_successful_positioning(self):
        for failure in ('press', 'release'):
            with self.subTest(failure=failure):
                driver = Mock()
                getattr(driver, failure).return_value = False
                handler = self._handler(driver)
                with patch('module.device.win.virtual_mouse.input.time.sleep'):
                    with self.assertRaises(RequestHumanTakeover):
                        for _ in range(FAILURE_LIMIT):
                            handler.mouse_click(120, 340)

    def test_interrupted_hold_still_releases_left_button(self):
        driver = Mock()
        handler = self._handler(driver)
        with patch('module.device.win.virtual_mouse.input.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                handler.press_mouse()
        driver.release.assert_called_once_with()

    def test_failed_drag_waypoint_stops_and_releases_left_button(self):
        for successful_moves in (1, 2):
            with self.subTest(successful_moves=successful_moves):
                driver = Mock()
                driver.move_to.side_effect = [True] * successful_moves + [False] * 40
                handler = self._handler(driver)
                with patch('module.device.win.virtual_mouse.input.time.sleep'):
                    handler.mouse_swipe((100, 100), (100, 350), speed=5)
                self.assertEqual(driver.move_to.call_count, successful_moves + 1)
                driver.release.assert_called_once_with()
                self.assertEqual(handler._failures, 1)

    def test_successful_complete_gesture_resets_failure_count(self):
        for method, args in (('press_mouse', ()), ('mouse_swipe', ((100, 100), (100, 350)))):
            with self.subTest(method=method):
                handler = self._handler()
                handler._failures = FAILURE_LIMIT - 1
                with patch('module.device.win.virtual_mouse.input.time.sleep'):
                    getattr(handler, method)(*args)
                self.assertEqual(handler._failures, 0)

    def test_driver_reopens_once_and_resends_the_same_report(self):
        driver = VirtualMouseDevice()
        driver._handle = 42
        with (
            patch.object(driver, '_ioctl', side_effect=[1, 0]) as ioctl,
            patch.object(driver, 'close') as close,
            patch.object(driver, 'open', return_value=True) as opened,
        ):
            self.assertTrue(driver.send(buttons=BTN_LEFT, dx=-2, wheel=-1))
        close.assert_called_once_with()
        opened.assert_called_once_with()
        self.assertEqual(ioctl.call_count, 2)
        self.assertEqual(ioctl.call_args_list[0], ioctl.call_args_list[1])

    def test_driver_stops_after_reopen_failure(self):
        driver = VirtualMouseDevice()
        driver._handle = 42
        with (
            patch.object(driver, '_ioctl', return_value=1) as ioctl,
            patch.object(driver, 'close'),
            patch.object(driver, 'open', return_value=False),
        ):
            self.assertFalse(driver.send(dx=20))
        ioctl.assert_called_once()

    def test_closed_loop_converges_with_acceleration_and_overshoot(self):
        position = [100, 100]
        driver = Mock()

        def accelerated_move(buttons=0, dx=0, dy=0):
            position[0] += dx * 2
            position[1] += dy * 2
            return True

        driver.send.side_effect = accelerated_move
        mouse = VirtualMouse(driver)
        with (
            patch.object(mouse, 'cursor', side_effect=lambda: tuple(position)),
            patch('module.device.win.virtual_mouse.driver_mouse.time.sleep'),
        ):
            self.assertTrue(mouse.move_to(165, 145, buttons=BTN_LEFT))
        self.assertLessEqual(abs(position[0] - 165), 2)
        self.assertLessEqual(abs(position[1] - 145), 2)
        self.assertTrue(all(item.kwargs['buttons'] == BTN_LEFT for item in driver.send.call_args_list))

    def test_closed_loop_stops_when_device_rejects_movement(self):
        driver = Mock()
        driver.send.return_value = False
        mouse = VirtualMouse(driver)
        with patch.object(mouse, 'cursor', return_value=(100, 100)):
            self.assertFalse(mouse.move_to(200, 200))
        driver.send.assert_called_once()

    def test_wheel_emits_one_signed_report_per_notch(self):
        driver = Mock()
        mouse = VirtualMouse(driver)
        with patch('module.device.win.virtual_mouse.driver_mouse.time.sleep'):
            self.assertTrue(mouse.wheel(-3))
            self.assertTrue(mouse.wheel(2))
            self.assertTrue(mouse.wheel(0))
        self.assertEqual(driver.send.call_args_list, [call(wheel=-1)] * 3 + [call(wheel=1)] * 2)

    # ------------------------------------------------------------------
    # HID 子设备判据（幽灵设备 / Windows 代码 45）
    # ------------------------------------------------------------------
    def test_sub_device_present_true_when_a_present_hid_device_is_enumerated(self):
        with patch.object(virtual_mouse_driver, '_device_instance_ids',
                          return_value=['LGHUBDEVICE\\VID_046D&PID_C231']):
            self.assertIs(virtual_mouse_driver.sub_device_present(), True)

    def test_sub_device_present_false_when_all_records_are_phantom(self):
        with patch.object(virtual_mouse_driver, '_device_instance_ids',
                          side_effect=[[], ['LGHUBDEVICE\\VID_046D&PID_C231']]):
            self.assertIs(virtual_mouse_driver.sub_device_present(), False)

    def test_sub_device_present_none_when_the_enumerator_does_not_exist(self):
        with patch.object(virtual_mouse_driver, '_device_instance_ids', return_value=[]):
            self.assertIsNone(virtual_mouse_driver.sub_device_present())

    def test_sub_device_present_queries_present_first_then_falls_back_to_all(self):
        with patch.object(virtual_mouse_driver, '_device_instance_ids',
                          side_effect=[[], ['ghost']]) as ids:
            virtual_mouse_driver.sub_device_present()
        self.assertEqual(ids.call_args_list, [
            call(virtual_mouse_driver.VIRTUAL_HID_ENUMERATOR, present_only=True),
            call(virtual_mouse_driver.VIRTUAL_HID_ENUMERATOR),
        ])

    def test_install_driver_rejects_a_phantom_hid_device(self):
        # 接口能打开、安装器 exit 0，但报告投不出去：不能报成功
        with (
            patch.object(virtual_mouse_driver, '_run_manager', return_value=[{'status': 'success'}]),
            patch.object(virtual_mouse_driver, 'enum_interface_paths', return_value=[_DEVICE_PATH]),
            patch.object(virtual_mouse_driver, 'open_device', return_value=True),
            patch.object(virtual_mouse_driver, 'sub_device_present', return_value=False),
        ):
            with self.assertRaises(virtual_mouse_driver.VirtualMouseDriverError) as raised:
                virtual_mouse_driver.install_driver()
        self.assertIn('code 45', str(raised.exception))

    def test_install_driver_succeeds_when_the_sub_device_is_present(self):
        with (
            patch.object(virtual_mouse_driver, '_run_manager', return_value=[{'status': 'success'}]),
            patch.object(virtual_mouse_driver, 'enum_interface_paths', return_value=[_DEVICE_PATH]),
            patch.object(virtual_mouse_driver, 'open_device', return_value=True),
            patch.object(virtual_mouse_driver, 'sub_device_present', return_value=True),
            patch.object(virtual_mouse_driver.logger, 'info'),
        ):
            self.assertEqual(virtual_mouse_driver.install_driver(), {'reboot_required': False})

    def test_install_driver_keeps_legacy_result_when_the_sub_device_is_unknown(self):
        # 系统中没有该枚举器时判不出来（None），此时不做判据，行为与改动前一致
        with (
            patch.object(virtual_mouse_driver, '_run_manager', return_value=[{'status': 'success'}]),
            patch.object(virtual_mouse_driver, 'enum_interface_paths', return_value=[_DEVICE_PATH]),
            patch.object(virtual_mouse_driver, 'open_device', return_value=True),
            patch.object(virtual_mouse_driver, 'sub_device_present', return_value=None),
            patch.object(virtual_mouse_driver.logger, 'info'),
        ):
            self.assertEqual(virtual_mouse_driver.install_driver(), {'reboot_required': False})
