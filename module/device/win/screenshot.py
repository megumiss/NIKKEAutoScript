from ctypes import windll

import numpy as np
import pyautogui
import win32gui
import win32ui
from PIL import Image

try:
    import mss
except ModuleNotFoundError:
    mss = None


class Screenshot:
    PW_RENDERFULLCONTENT = 0x00000002

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
    def get_window(title, class_name, hwnd):
        windows = pyautogui.getWindowsWithTitle(title)
        for window in windows:
            if window.title != title:
                continue
            if hwnd is not None and getattr(window, '_hWnd', None) != hwnd:
                continue
            if class_name and win32gui.GetClassName(window._hWnd) != class_name:
                continue
            return window
        return False

    @staticmethod
    def _virtual_screen_origin(screens):
        if not screens:
            return 0, 0
        from win32api import EnumDisplayMonitors, GetMonitorInfo

        monitors = [GetMonitorInfo(m[0])['Monitor'] for m in EnumDisplayMonitors()]
        min_x = min(m[0] for m in monitors)
        min_y = min(m[1] for m in monitors)
        return min_x, min_y

    @staticmethod
    def _capture_pyautogui(capture_left, capture_top, capture_width, capture_height, screens):
        min_x, min_y = Screenshot._virtual_screen_origin(screens)
        region = (
            int(capture_left - min_x),
            int(capture_top - min_y),
            int(capture_width),
            int(capture_height),
        )
        image = pyautogui.screenshot(region=region, allScreens=screens)
        return np.array(image)

    @staticmethod
    def _capture_mss(capture_left, capture_top, capture_width, capture_height):
        if mss is None:
            raise ModuleNotFoundError('mss is not installed')
        with mss.MSS() as sct:
            shot = sct.grab(
                {
                    'left': int(capture_left),
                    'top': int(capture_top),
                    'width': int(capture_width),
                    'height': int(capture_height),
                }
            )
            image = np.asarray(shot)[:, :, :3]  # BGR
            return image[:, :, ::-1].copy()  # RGB

    @staticmethod
    def _capture_printwindow(window, capture_left, capture_top, capture_width, capture_height):
        win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(window._hWnd)
        win_width = win_right - win_left
        win_height = win_bottom - win_top
        crop_x = int(capture_left - win_left)
        crop_y = int(capture_top - win_top)
        crop_right = crop_x + int(capture_width)
        crop_bottom = crop_y + int(capture_height)

        hwnd_dc = win32gui.GetWindowDC(window._hWnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, win_width, win_height)
        save_dc.SelectObject(bitmap)
        result = 0

        try:
            result = windll.user32.PrintWindow(window._hWnd, save_dc.GetSafeHdc(), Screenshot.PW_RENDERFULLCONTENT)
            if result != 1:
                result = windll.user32.PrintWindow(window._hWnd, save_dc.GetSafeHdc(), 0)
            if result != 1:
                raise RuntimeError('PrintWindow failed')

            bmp_info = bitmap.GetInfo()
            bmp_data = bitmap.GetBitmapBits(True)
            full_image = np.frombuffer(bmp_data, dtype=np.uint8).reshape((bmp_info['bmHeight'], bmp_info['bmWidth'], 4))
            full_image = full_image[:, :, :3][:, :, ::-1]  # BGRA -> RGB

            image = full_image[crop_y:crop_bottom, crop_x:crop_right]
            if image.size == 0:
                raise RuntimeError('PrintWindow image crop is empty')
            if image.shape[1] != capture_width or image.shape[0] != capture_height:
                raise RuntimeError(
                    f'PrintWindow image crop size mismatch: expected {capture_width}x{capture_height}, '
                    f'got {image.shape[1]}x{image.shape[0]}'
                )
            return image
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(window._hWnd, hwnd_dc)

    @staticmethod
    def take_screenshot(
        title,
        resolution,
        screens=False,
        crop=(0, 0, 1, 1),
        screenshot_method='pyautogui',
        class_name=None,
        *,
        hwnd,
    ):
        window = Screenshot.get_window(title, class_name=class_name, hwnd=hwnd)
        if window:
            left, top, width, height = Screenshot.get_window_region(window)

            capture_left = int(left + width * crop[0])
            capture_top = int(top + height * crop[1])
            capture_width = int(width * crop[2])
            capture_height = int(height * crop[3])
            if capture_width <= 0 or capture_height <= 0:
                return False

            method = str(screenshot_method or 'pyautogui')
            if method == 'pyautogui':
                image = Screenshot._capture_pyautogui(capture_left, capture_top, capture_width, capture_height, screens)
            elif method == 'mss':
                image = Screenshot._capture_mss(capture_left, capture_top, capture_width, capture_height)
            elif method == 'PrintWindow':
                image = Screenshot._capture_printwindow(
                    window=window,
                    capture_left=capture_left,
                    capture_top=capture_top,
                    capture_width=capture_width,
                    capture_height=capture_height,
                )
            else:
                raise ValueError(f'Unknown PC screenshot method: {method}')

            real_width, _ = Screenshot.get_window_real_resolution(window)
            if real_width > resolution[0]:
                screenshot_scale_factor = resolution[0] / real_width
            else:
                screenshot_scale_factor = 1

            screenshot_pos = (
                capture_left,
                capture_top,
                int(capture_width * screenshot_scale_factor),
                int(capture_height * screenshot_scale_factor),
            )

            if screenshot_scale_factor != 1:
                image = np.array(Image.fromarray(image).resize((screenshot_pos[2], screenshot_pos[3])))

            return image, screenshot_pos, screenshot_scale_factor

        return False
