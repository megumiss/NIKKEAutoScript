import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.device.win.screenshot import Screenshot


def _window(title, hwnd):
    return SimpleNamespace(title=title, _hWnd=hwnd)


class WinScreenshotTests(unittest.TestCase):
    def test_get_window_requires_exact_title(self):
        editor = _window('NIKKEAutoScript - Visual Studio Code', 1)
        game = _window('NIKKE', 2)

        with patch('module.device.win.screenshot.pyautogui.getWindowsWithTitle', return_value=[editor, game]):
            self.assertIs(Screenshot.get_window('NIKKE', class_name=None, hwnd=2), game)

    def test_get_window_requires_matching_class_name(self):
        other = _window('NIKKE', 1)
        game = _window('NIKKE', 2)

        with (
            patch('module.device.win.screenshot.pyautogui.getWindowsWithTitle', return_value=[other, game]),
            patch('module.device.win.screenshot.win32gui.GetClassName', return_value='UnityWndClass'),
        ):
            self.assertIs(Screenshot.get_window('NIKKE', class_name='UnityWndClass', hwnd=2), game)

    def test_get_window_requires_matching_hwnd(self):
        other = _window('NIKKE', 1)
        game = _window('NIKKE', 2)

        with patch('module.device.win.screenshot.pyautogui.getWindowsWithTitle', return_value=[other, game]):
            self.assertIs(Screenshot.get_window('NIKKE', class_name=None, hwnd=2), game)
