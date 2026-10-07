from ctypes import windll

import threading
import time

import numpy as np
import pyautogui
import win32gui
import win32ui
from PIL import Image

try:
    import mss
except ModuleNotFoundError:
    mss = None


class WgcCaptureSession:
    """
    单个窗口的 Windows.Graphics.Capture 捕获会话。
    后台线程持续收帧，只保留最新一帧的私有拷贝，帧缓冲有界。
    """

    def __init__(self, hwnd):
        try:
            from windows_capture import WindowsCapture
        except ModuleNotFoundError:
            raise RuntimeError('windows-capture is not installed, run: pip install windows-capture')

        self.hwnd = hwnd
        self.lock = threading.Lock()
        self.latest = None
        self.failed = False

        # WGC 按需出帧，50ms 上限足够覆盖 0.3s 的截图间隔，避免空转收满 60fps
        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            minimum_update_interval=50,
            window_hwnd=int(hwnd),
        )

        @capture.event
        def on_frame_arrived(frame, capture_control):
            # frame_buffer 是原生映射内存的视图，回调返回后可能失效，必须拷贝
            with self.lock:
                self.latest = frame.frame_buffer.copy()

        @capture.event
        def on_closed():
            self.failed = True

        self.control = capture.start_free_threaded()

    def read(self, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if self.latest is not None:
                    return self.latest
            if self.failed:
                raise RuntimeError('Windows.Graphics.Capture session closed')
            time.sleep(0.05)
        raise RuntimeError('Windows.Graphics.Capture received no frame')


_wgc_sessions = {}
_wgc_sessions_lock = threading.Lock()


def _wgc_session(hwnd):
    with _wgc_sessions_lock:
        session = _wgc_sessions.get(hwnd)
        if session is None or session.failed or session.control.is_finished():
            _wgc_sessions.pop(hwnd, None)
            session = WgcCaptureSession(hwnd)
            _wgc_sessions[hwnd] = session
        return session


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
    def _capture_wgc(window, capture_left, capture_top, capture_width, capture_height):
        import ctypes
        from ctypes import wintypes

        hwnd = window._hWnd
        frame = _wgc_session(hwnd).read()

        # WGC 帧覆盖窗口可见帧（含标题栏），物理像素，原点 = DWM 扩展帧边界原点。
        # 进程 DPI 不感知时调用方坐标是逻辑像素，需要按窗口 DPI 换算到物理像素。
        awareness = ctypes.c_int()
        if ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness)) != 0:
            awareness.value = 2
        if awareness.value == 0:
            scale = ctypes.windll.user32.GetDpiForWindow(wintypes.HWND(hwnd)) / 96
        else:
            scale = 1.0

        rect = wintypes.RECT()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint32(9), ctypes.byref(rect), ctypes.sizeof(rect)
        )
        if hr != 0:
            raise RuntimeError(f'DwmGetWindowAttribute failed: {hr}')

        if frame.shape[1] != rect.right - rect.left or frame.shape[0] != rect.bottom - rect.top:
            # 窗口 resize 过渡期内帧尺寸滞后于 EFB，抛错让上层重试
            raise RuntimeError(
                f'Windows.Graphics.Capture frame size {frame.shape[1]}x{frame.shape[0]} '
                f'does not match window frame {rect.right - rect.left}x{rect.bottom - rect.top}'
            )

        crop_x = int(round(capture_left * scale)) - rect.left
        crop_y = int(round(capture_top * scale)) - rect.top
        crop_right = crop_x + int(round(capture_width * scale))
        crop_bottom = crop_y + int(round(capture_height * scale))
        if crop_x < 0 or crop_y < 0 or crop_right > frame.shape[1] or crop_bottom > frame.shape[0]:
            raise RuntimeError(
                f'Windows.Graphics.Capture crop ({crop_x}, {crop_y}, {crop_right}, {crop_bottom}) '
                f'out of frame {frame.shape[1]}x{frame.shape[0]}'
            )

        image = frame[crop_y:crop_bottom, crop_x:crop_right, :3]
        if image.size == 0:
            raise RuntimeError('Windows.Graphics.Capture image crop is empty')
        if scale != 1.0:
            image = np.array(Image.fromarray(image).resize((int(capture_width), int(capture_height))))
        return image[:, :, ::-1]  # BGRA -> RGB

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
            elif method == 'capture':
                image = Screenshot._capture_wgc(
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
