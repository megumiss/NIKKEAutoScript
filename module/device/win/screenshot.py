import numpy as np
import pyautogui
import win32gui
from desktopmagic.screengrab_win32 import getDisplayRects


class Screenshot:
    @staticmethod
    def is_application_fullscreen(window):
        screen_width, screen_height = pyautogui.size()
        return (window.width, window.height) == (screen_width, screen_height)

    @staticmethod
    def get_window_real_resolution(window):
        left, top, right, bottom = win32gui.GetClientRect(window._hWnd)
        return right - left, bottom - top

    @staticmethod
    def get_window_region(window):
        if Screenshot.is_application_fullscreen(window):
            return (window.left, window.top, window.width, window.height)
        else:
            real_width, real_height = Screenshot.get_window_real_resolution(window)
            other_border = (window.width - real_width) // 2
            up_border = window.height - real_height - other_border
            return (
                window.left + other_border,
                window.top + up_border,
                window.width - other_border - other_border,
                window.height - up_border - other_border,
            )

    @staticmethod
    def get_window(title):
        windows = pyautogui.getWindowsWithTitle(title)
        if windows:
            window = windows[0]
            return window
        return False

    @staticmethod
    def take_screenshot(title, resolution, screens=False, crop=(0, 0, 1, 1)):
        window = Screenshot.get_window(title)
        if window:
            left, top, width, height = Screenshot.get_window_region(window)

            # 获取所有屏幕的最小x/y（可能是负数）
            if screens:
                from win32api import EnumDisplayMonitors, GetMonitorInfo

                monitors = [GetMonitorInfo(m[0])['Monitor'] for m in EnumDisplayMonitors()]
                min_x = min(m[0] for m in monitors)
                min_y = min(m[1] for m in monitors)
            else:
                min_x = min_y = 0

            # 将坐标平移，使得 pyautogui.screenshot 的 region 永远为正
            region = (
                int(left - min_x + width * crop[0]),
                int(top - min_y + height * crop[1]),
                int(width * crop[2]),
                int(height * crop[3]),
            )
            screenshot = pyautogui.screenshot(region=region, allScreens=screens)

            real_width, _ = Screenshot.get_window_real_resolution(window)
            if real_width > resolution[0]:
                screenshot_scale_factor = resolution[0] / real_width
                screenshot = screenshot.resize((int(resolution[0] * crop[2]), int(resolution[1] * crop[3])))
            else:
                screenshot_scale_factor = 1

            screenshot_pos = (
                int(left + width * crop[0]),
                int(top + height * crop[1]),
                int(width * crop[2] * screenshot_scale_factor),
                int(height * crop[3] * screenshot_scale_factor),
            )

            return np.array(screenshot), screenshot_pos, screenshot_scale_factor

        return False
