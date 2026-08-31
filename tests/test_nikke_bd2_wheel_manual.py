"""Run the ok-bd2 mouse-wheel probe against the visible NIKKE game window.

Run this file from an elevated PowerShell terminal. The probe does not bring
NIKKE to the foreground; it sends the same click + PostMessage wheel sequence
used by the ok-bd2 wheel test. Screenshots are captured with PrintWindow.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from datetime import datetime
from ctypes import wintypes

import numpy as np
import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image, ImageDraw


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PW_RENDERFULLCONTENT = 0x00000002
RECT = wintypes.RECT
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
NIKKE_REFERENCE_SIZE = (720, 1280)
SHOP_SCROLL_DOWN = ((505, 1000), (505, 700))
SHOP_SCROLL_UP = ((505, 700), (505, 1000))


def enable_dpi_awareness() -> str:
    """Use physical pixels for window rectangles, screenshots, and cursor positions."""
    user32 = ctypes.windll.user32
    try:
        set_thread_context = user32.SetThreadDpiAwarenessContext
        set_thread_context.argtypes = [ctypes.c_void_p]
        set_thread_context.restype = ctypes.c_void_p
        if set_thread_context(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return 'per-monitor-v2-thread'
    except (AttributeError, OSError):
        pass

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return 'per-monitor-process'
    except (AttributeError, OSError):
        if ctypes.windll.user32.SetProcessDPIAware():
            return 'system-process'
    raise RuntimeError('Unable to enable DPI awareness')


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def process_path(hwnd: int) -> str:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ''
    try:
        buffer_size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(buffer_size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(buffer_size)
        ):
            return ''
        return buffer.value
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def find_game_window(expected_path: str | None) -> int:
    candidates: list[int] = []

    def callback(hwnd: int, _lparam: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetWindowText(hwnd) != 'NIKKE':
            return True
        if win32gui.GetClassName(hwnd) != 'UnityWndClass':
            return True
        candidates.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    if expected_path:
        expected = os.path.normcase(os.path.abspath(expected_path))
        for hwnd in candidates:
            if os.path.normcase(os.path.abspath(process_path(hwnd))) == expected:
                return hwnd
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError('No visible NIKKE UnityWndClass window found')
    details = [(hwnd, process_path(hwnd), win32gui.GetWindowRect(hwnd)) for hwnd in candidates]
    raise RuntimeError(f'Multiple NIKKE windows found; use --game-path. Candidates: {details}')


def client_geometry(hwnd: int) -> tuple[int, int, int, int]:
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    _, _, width, height = win32gui.GetClientRect(hwnd)
    return left, top, width, height


def capture_printwindow(hwnd: int, output_path: str) -> np.ndarray:
    window_left, window_top, window_right, window_bottom = win32gui.GetWindowRect(hwnd)
    window_width = window_right - window_left
    window_height = window_bottom - window_top
    client_left, client_top, client_width, client_height = client_geometry(hwnd)

    window_dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(window_dc)
    memory_dc = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, window_width, window_height)
    memory_dc.SelectObject(bitmap)
    try:
        result = ctypes.windll.user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if result != 1:
            result = ctypes.windll.user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), 0)
        if result != 1:
            raise RuntimeError('PrintWindow failed')

        info = bitmap.GetInfo()
        data = bitmap.GetBitmapBits(True)
        image = np.frombuffer(data, dtype=np.uint8).reshape((info['bmHeight'], info['bmWidth'], 4))
        image = image[:, :, :3][:, :, ::-1]
        crop_x = client_left - window_left
        crop_y = client_top - window_top
        image = image[crop_y:crop_y + client_height, crop_x:crop_x + client_width].copy()
        if image.shape[:2] != (client_height, client_width):
            raise RuntimeError(f'PrintWindow crop size mismatch: {image.shape}')
        Image.fromarray(image).save(output_path)
        return image
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)


def save_point_preview(image: np.ndarray, point: tuple[int, int], output_path: str) -> None:
    preview = Image.fromarray(image.copy())
    draw = ImageDraw.Draw(preview)
    x, y = point
    radius = max(8, round(min(preview.size) * 0.02))
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(255, 0, 0), width=3)
    draw.line((x - radius * 2, y, x + radius * 2, y), fill=(255, 0, 0), width=2)
    draw.line((x, y - radius * 2, x, y + radius * 2), fill=(255, 0, 0), width=2)
    preview.save(output_path)


def post_message(hwnd: int, message: int, wparam: int, lparam: int) -> str:
    try:
        win32gui.PostMessage(hwnd, message, wparam, lparam)
        return 'ok'
    except Exception as error:
        return f'{type(error).__name__}: {error}'


def project_scroll_parameters(
    start: tuple[int, int],
    end: tuple[int, int],
) -> tuple[tuple[float, float], int, int]:
    """Mirror Automation.swipe(method='scroll') for a 720x1280 reference frame."""
    start_x, start_y = start
    end_x, end_y = end
    horizontal = abs(end_x - start_x) > abs(end_y - start_y)
    pixel_distance = end_x - start_x if horizontal else end_y - start_y
    if not pixel_distance:
        pixel_distance = end_x - start_x
    scroll_count = round(abs(pixel_distance) / 65) - 1
    if horizontal:
        direction = 1 if pixel_distance < 0 else -1
    else:
        direction = -1 if pixel_distance < 0 else 1
    reference_width, reference_height = NIKKE_REFERENCE_SIZE
    relative_point = (
        ((start_x + end_x) // 2) / reference_width,
        ((start_y + end_y) // 2) / reference_height,
    )
    return relative_point, direction, scroll_count


def get_cursor_clip() -> tuple[int, int, int, int] | None:
    rect = RECT()
    if ctypes.windll.user32.GetClipCursor(ctypes.byref(rect)):
        if (rect.left, rect.top, rect.right, rect.bottom) != (0, 0, 0, 0):
            return rect.left, rect.top, rect.right, rect.bottom
    return None


def set_cursor_clip(clip: tuple[int, int, int, int] | None) -> None:
    if clip is None:
        ctypes.windll.user32.ClipCursor(None)
        return
    rect = RECT(*clip)
    ctypes.windll.user32.ClipCursor(ctypes.byref(rect))


def run_probe(
    hwnd: int,
    output_dir: str,
    prefix: str,
    relative_point: tuple[float, float],
    direction: int,
    strict_cursor: bool = False,
) -> dict:
    client_left, client_top, width, height = client_geometry(hwnd)
    client_x = round(max(0.0, min(1.0, relative_point[0])) * width)
    client_y = round(max(0.0, min(1.0, relative_point[1])) * height)
    screen_x, screen_y = win32gui.ClientToScreen(hwnd, (client_x, client_y))
    click_lparam = win32api.MAKELONG(client_x, client_y)
    wheel_lparam = win32api.MAKELONG(screen_x, screen_y)
    wheel_wparam = win32api.MAKELONG(0, win32con.WHEEL_DELTA * direction)
    old_cursor = win32api.GetCursorPos()
    old_clip = get_cursor_clip()

    before_path = os.path.join(output_dir, f'{prefix}_00_before.png')
    before = capture_printwindow(hwnd, before_path)
    point_preview_path = os.path.join(output_dir, f'{prefix}_00_test_point.png')
    save_point_preview(before, (client_x, client_y), point_preview_path)
    foreground_before = win32gui.GetForegroundWindow()
    click_results: list[str] = []
    wheel_results: list[dict] = []
    block_input = False
    cursor_move_result = 'ok'
    try:
        # Some foreground applications clip the cursor to their own window.
        # Clear that restriction before moving to the background game.
        set_cursor_clip(None)
        try:
            win32api.SetCursorPos((screen_x, screen_y))
        except Exception as error:
            cursor_move_result = f'{type(error).__name__}: {error}'
            if strict_cursor:
                raise
            print(f'WARNING: cursor move failed: {cursor_move_result}; continuing with PostMessage')
        time.sleep(0.025)
        block_input = bool(ctypes.windll.user32.BlockInput(True))
        click_results.append(post_message(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, click_lparam))
        time.sleep(0.02)
        click_results.append(post_message(hwnd, win32con.WM_LBUTTONUP, 0, click_lparam))

        for index in range(1, 10):
            post_result = post_message(hwnd, win32con.WM_MOUSEWHEEL, wheel_wparam, wheel_lparam)
            time.sleep(0.15)
            path = os.path.join(output_dir, f'{prefix}_{index:02d}.png')
            frame = capture_printwindow(hwnd, path)
            wheel_results.append(
                {
                    'index': index,
                    'post_result': post_result,
                    'foreground': win32gui.GetForegroundWindow(),
                    'pixel_delta_from_before': float(
                        np.abs(frame.astype(np.int16) - before.astype(np.int16)).mean()
                    ),
                    'screenshot': path,
                }
            )
    finally:
        win32api.SetCursorPos(old_cursor)
        if block_input:
            ctypes.windll.user32.BlockInput(False)
        set_cursor_clip(old_clip)

    return {
        'prefix': prefix,
        'relative_point': relative_point,
        'client_point': [client_x, client_y],
        'screen_point': [screen_x, screen_y],
        'test_point_preview': point_preview_path,
        'direction': direction,
        'count': 9,
        'interval_seconds': 0.1,
        'foreground_before': foreground_before,
        'foreground_after': win32gui.GetForegroundWindow(),
        'click_results': click_results,
        'block_input_succeeded': block_input,
        'cursor_move_result': cursor_move_result,
        'steps': wheel_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', default='', help='Directory for screenshots and result.json')
    parser.add_argument('--game-path', default='', help='Optional exact path to nikke.exe')
    parser.add_argument(
        '--strict-cursor',
        action='store_true',
        help='Abort if SetCursorPos fails; default is to continue PostMessage testing.',
    )
    args = parser.parse_args()

    if not is_admin():
        raise SystemExit('This script must be run from an elevated PowerShell (IsUserAnAdmin=False).')
    dpi_awareness = enable_dpi_awareness()
    output_dir = args.output_dir or os.path.join(os.getcwd(), 'tmp', 'nikke-bd2-wheel-test')
    os.makedirs(output_dir, exist_ok=True)
    hwnd = find_game_window(args.game_path)
    if win32gui.IsIconic(hwnd):
        raise SystemExit('NIKKE is minimized; restore it before running the test.')

    _, _, width, height = client_geometry(hwnd)
    if width >= height:
        raise SystemExit(f'NIKKE client is not portrait: {width}x{height}')
    down_point, down_direction, down_project_count = project_scroll_parameters(*SHOP_SCROLL_DOWN)
    up_point, up_direction, up_project_count = project_scroll_parameters(*SHOP_SCROLL_UP)
    result = {
        'timestamp': datetime.now().isoformat(),
        'is_admin': is_admin(),
        'dpi_awareness': dpi_awareness,
        'hwnd': hwnd,
        'process_path': process_path(hwnd),
        'window_rect': list(win32gui.GetWindowRect(hwnd)),
        'client_size': [width, height],
        'reference_size': list(NIKKE_REFERENCE_SIZE),
        'project_scroll_actions': [
            {
                'name': 'shop_scan_down',
                'start': list(SHOP_SCROLL_DOWN[0]),
                'end': list(SHOP_SCROLL_DOWN[1]),
                'direction': down_direction,
                'project_scroll_count': down_project_count,
            },
            {
                'name': 'shop_reset_up',
                'start': list(SHOP_SCROLL_UP[0]),
                'end': list(SHOP_SCROLL_UP[1]),
                'direction': up_direction,
                'project_scroll_count': up_project_count,
            },
        ],
        'initial_foreground': win32gui.GetForegroundWindow(),
        'probes': [
            run_probe(hwnd, output_dir, 'nikke_shop_down', down_point, down_direction, args.strict_cursor),
            run_probe(hwnd, output_dir, 'nikke_shop_up', up_point, up_direction, args.strict_cursor),
        ],
        'final_foreground': win32gui.GetForegroundWindow(),
    }
    result_path = os.path.join(output_dir, 'result.json')
    with open(result_path, 'w', encoding='utf-8') as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'\nSaved screenshots and result to: {output_dir}')


if __name__ == '__main__':
    main()
