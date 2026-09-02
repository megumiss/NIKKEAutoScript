import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.device.win.app_control import AppControl
from module.device.win.automation import Automation
from module.device.win.input import Input
from module.device.win.ok_interaction.hwnd_window import HwndWindowAdapter
from module.device.win.ok_interaction.input import PostMessageInput
from module.device.win.ok_interaction.post_message import PostMessageInteraction


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
