"""Test cursor-assisted background-message dragging against NIKKE's scrap shop.

The game remains in the background. The test moves the real cursor along the
drag path while sending WM_MOUSEMOVE/WM_LBUTTONDOWN/WM_LBUTTONUP directly to
NIKKE, matching the cursor-assisted background click strategy.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from datetime import datetime

import numpy as np
import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PW_RENDERFULLCONTENT = 0x00000002
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
REFERENCE_SIZE = (720, 1280)
SHOP_SWIPE_DOWN = ((505, 1000), (505, 700))
SHOP_SWIPE_UP = ((505, 700), (505, 1000))


def enable_dpi_awareness() -> str:
    user32 = ctypes.windll.user32
    try:
        set_context = user32.SetThreadDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_void_p
        if set_context(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return 'per-monitor-v2-thread'
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return 'per-monitor-process'
    except (AttributeError, OSError):
        if user32.SetProcessDPIAware():
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
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ''
        return buffer.value
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def find_game_window(expected_path: str) -> int:
    candidates: list[int] = []

    def callback(hwnd: int, _lparam: object) -> bool:
        if (
            win32gui.IsWindowVisible(hwnd)
            and win32gui.GetWindowText(hwnd) == 'NIKKE'
            and win32gui.GetClassName(hwnd) == 'UnityWndClass'
        ):
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
        raise RuntimeError('No visible NIKKE game window found')
    details = [(hwnd, process_path(hwnd), win32gui.GetWindowRect(hwnd)) for hwnd in candidates]
    raise RuntimeError(f'Multiple NIKKE windows found; use --game-path. Candidates: {details}')


def client_geometry(hwnd: int) -> tuple[int, int, int, int]:
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    _, _, width, height = win32gui.GetClientRect(hwnd)
    return left, top, width, height


def capture_printwindow(hwnd: int, output_path: str) -> np.ndarray:
    window_left, window_top, window_right, window_bottom = win32gui.GetWindowRect(hwnd)
    window_width, window_height = window_right - window_left, window_bottom - window_top
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
            raise RuntimeError(
                'PrintWindow failed; run this script from an elevated PowerShell '
                'when NIKKE is running at a higher integrity level.'
            )
        info = bitmap.GetInfo()
        data = bitmap.GetBitmapBits(True)
        image = np.frombuffer(data, dtype=np.uint8).reshape((info['bmHeight'], info['bmWidth'], 4))
        image = image[:, :, :3][:, :, ::-1]
        crop_x, crop_y = client_left - window_left, client_top - window_top
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


def to_screen(hwnd: int, point: tuple[int, int]) -> tuple[int, int]:
    return win32gui.ClientToScreen(hwnd, point)


def target_client_point(
    hwnd: int,
    point: tuple[int, int],
) -> tuple[int, tuple[int, int], tuple[int, int]]:
    """Resolve a client point to the same dynamic child target as ok-script."""
    screen_point = to_screen(hwnd, point)
    target_hwnd = hwnd
    for child in _visible_children(hwnd):
        left, top, right, bottom = win32gui.GetWindowRect(child)
        if left <= screen_point[0] < right and top <= screen_point[1] < bottom:
            target_hwnd = child
            break
    local_point = win32gui.ScreenToClient(target_hwnd, screen_point)
    return target_hwnd, screen_point, local_point


def _visible_children(hwnd: int) -> list[int]:
    children: list[int] = []

    def callback(child: int, _lparam: object) -> bool:
        if win32gui.IsWindowVisible(child):
            children.append(child)
        return True

    win32gui.EnumChildWindows(hwnd, callback, None)
    return children


def post_message_drag(
    hwnd: int,
    start: tuple[int, int],
    end: tuple[int, int],
    duration_ms: int,
) -> dict:
    start_target, start_screen, start_local = target_client_point(hwnd, start)
    end_target, end_screen, end_local = target_client_point(hwnd, end)
    steps = max(6, round(duration_ms / 30))
    foreground_before = win32gui.GetForegroundWindow()
    messages: list[dict] = []
    last_target, last_local = start_target, start_local
    old_cursor = win32api.GetCursorPos()
    block_input_succeeded = False
    cursor_move_error = None

    def post(target: int, message: int, w_param: int, point: tuple[int, int]) -> None:
        try:
            win32gui.PostMessage(target, message, w_param, win32api.MAKELONG(*point))
            messages.append(
                {
                    'target_hwnd': target,
                    'message': message,
                    'point': list(point),
                    'error': None,
                }
            )
        except Exception as error:
            messages.append(
                {
                    'target_hwnd': target,
                    'message': message,
                    'point': list(point),
                    'error': f'{type(error).__name__}: {error}',
                }
            )

    def activate(target: int) -> None:
        # Match ok-script's try_activate() before every move/update.
        win32gui.PostMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
        if target != hwnd:
            win32gui.PostMessage(target, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)

    try:
        block_input_succeeded = bool(ctypes.windll.user32.BlockInput(True))
        try:
            win32api.SetCursorPos(start_screen)
        except Exception as error:
            cursor_move_error = f'{type(error).__name__}: {error}'
        activate(start_target)
        post(start_target, win32con.WM_MOUSEMOVE, 0, start_local)
        time.sleep(0.1)
        activate(start_target)
        post(start_target, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, start_local)
        for index in range(1, steps + 1):
            ratio = index / steps
            client_point = (
                round(start[0] + (end[0] - start[0]) * ratio),
                round(start[1] + (end[1] - start[1]) * ratio),
            )
            target, screen_point, local_point = target_client_point(hwnd, client_point)
            try:
                win32api.SetCursorPos(screen_point)
            except Exception as error:
                cursor_move_error = f'{type(error).__name__}: {error}'
            activate(target)
            post(target, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, local_point)
            last_target, last_local = target, local_point
            time.sleep(duration_ms / 1000 / steps)
    finally:
        post(last_target, win32con.WM_LBUTTONUP, 0, last_local)
        try:
            win32api.SetCursorPos(old_cursor)
        except Exception as error:
            cursor_move_error = cursor_move_error or f'{type(error).__name__}: {error}'
        if block_input_succeeded:
            ctypes.windll.user32.BlockInput(False)
    return {
        'start_client': list(start),
        'end_client': list(end),
        'start_screen': list(start_screen),
        'end_screen': list(end_screen),
        'start_target_hwnd': start_target,
        'end_target_hwnd': end_target,
        'duration_milliseconds': duration_ms,
        'steps': steps,
        'foreground_before': foreground_before,
        'foreground_after': win32gui.GetForegroundWindow(),
        'physical_cursor_moved': True,
        'original_cursor': list(old_cursor),
        'block_input_succeeded': block_input_succeeded,
        'cursor_move_error': cursor_move_error,
        'messages_sent': len(messages),
        'message_errors': [item for item in messages if item['error']],
    }


def run_probe(hwnd: int, output_dir: str, name: str, action: tuple[tuple[int, int], tuple[int, int]], duration_ms: int) -> dict:
    start, end = action
    before_path = os.path.join(output_dir, f'{name}_00_before.png')
    before = capture_printwindow(hwnd, before_path)
    drag_result = post_message_drag(hwnd, start, end, duration_ms)
    time.sleep(1.0)
    after_path = os.path.join(output_dir, f'{name}_01_after.png')
    after = capture_printwindow(hwnd, after_path)
    drag_result.update(
        {
            'name': name,
            'before_screenshot': before_path,
            'after_screenshot': after_path,
            'pixel_delta_mean': float(np.abs(after.astype(np.int16) - before.astype(np.int16)).mean()),
        }
    )
    return drag_result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', default='', help='Directory for screenshots and result.json')
    parser.add_argument('--game-path', default='', help='Optional exact path to nikke.exe')
    parser.add_argument(
        '--duration-ms',
        type=int,
        default=700,
        help='Cursor-assisted swipe duration in milliseconds (default: 700).',
    )
    args = parser.parse_args()
    if not is_admin():
        raise SystemExit(
            'Run this script from an elevated PowerShell; PrintWindow may be blocked '
            'when NIKKE runs at a higher integrity level.'
        )
    if args.duration_ms <= 0:
        raise SystemExit('--duration-ms must be positive')
    dpi_awareness = enable_dpi_awareness()
    output_dir = args.output_dir or os.path.join(os.getcwd(), 'tmp', 'nikke-bd2-swipe-test')
    os.makedirs(output_dir, exist_ok=True)
    hwnd = find_game_window(args.game_path)
    if win32gui.IsIconic(hwnd):
        raise SystemExit('NIKKE is minimized; restore it before testing.')
    _, _, width, height = client_geometry(hwnd)
    if (width, height) != REFERENCE_SIZE:
        raise SystemExit(f'Expected a 720x1280 client after DPI awareness, got {width}x{height}.')
    result = {
        'timestamp': datetime.now().isoformat(),
        'is_admin': is_admin(),
        'dpi_awareness': dpi_awareness,
        'hwnd': hwnd,
        'process_path': process_path(hwnd),
        'window_rect': list(win32gui.GetWindowRect(hwnd)),
        'client_size': [width, height],
        'reference_size': list(REFERENCE_SIZE),
        'project_actions': {
            'shop_scan_down': [list(SHOP_SWIPE_DOWN[0]), list(SHOP_SWIPE_DOWN[1])],
            'shop_reset_up': [list(SHOP_SWIPE_UP[0]), list(SHOP_SWIPE_UP[1])],
        },
        'initial_foreground': win32gui.GetForegroundWindow(),
        'probes': [
            run_probe(hwnd, output_dir, 'shop_down', SHOP_SWIPE_DOWN, args.duration_ms),
            run_probe(hwnd, output_dir, 'shop_up', SHOP_SWIPE_UP, args.duration_ms),
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
