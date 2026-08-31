"""Probe common Win32 messages accepted by the NIKKE window.

The probe never moves the physical cursor. It records PostMessage queue
status and, optionally, SendMessage return values. Run elevated when NIKKE
is elevated so PrintWindow can capture before/after frames.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from ctypes import wintypes
from datetime import datetime

import numpy as np
import win32api
import win32con
import win32gui

from test_nikke_bd2_swipe_manual import (
    REFERENCE_SIZE,
    capture_printwindow,
    client_geometry,
    enable_dpi_awareness,
    find_game_window,
    is_admin,
    process_path,
    target_client_point,
)


def message_name(message: int) -> str:
    names = {
        win32con.WM_ACTIVATE: 'WM_ACTIVATE',
        win32con.WM_MOUSEMOVE: 'WM_MOUSEMOVE',
        win32con.WM_LBUTTONDOWN: 'WM_LBUTTONDOWN',
        win32con.WM_LBUTTONUP: 'WM_LBUTTONUP',
        win32con.WM_RBUTTONDOWN: 'WM_RBUTTONDOWN',
        win32con.WM_RBUTTONUP: 'WM_RBUTTONUP',
        win32con.WM_MBUTTONDOWN: 'WM_MBUTTONDOWN',
        win32con.WM_MBUTTONUP: 'WM_MBUTTONUP',
        win32con.WM_MOUSEWHEEL: 'WM_MOUSEWHEEL',
        win32con.WM_KEYDOWN: 'WM_KEYDOWN',
        win32con.WM_KEYUP: 'WM_KEYUP',
        win32con.WM_CHAR: 'WM_CHAR',
        win32con.WM_NCMOUSEMOVE: 'WM_NCMOUSEMOVE',
    }
    return names.get(message, hex(message))


def post_message(hwnd: int, message: int, w_param: int, l_param: int) -> dict:
    try:
        result = win32gui.PostMessage(hwnd, message, w_param, l_param)
        return {'ok': True, 'return': result, 'error': None}
    except Exception as error:
        return {'ok': False, 'return': None, 'error': f'{type(error).__name__}: {error}'}


def send_message(hwnd: int, message: int, w_param: int, l_param: int, timeout_ms: int) -> dict:
    """Synchronously send with a timeout so a hung game cannot hang the probe."""
    result = ctypes.c_ssize_t()
    started = time.perf_counter()
    send_timeout = ctypes.windll.user32.SendMessageTimeoutW
    send_timeout.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_ssize_t),
    ]
    send_timeout.restype = ctypes.c_ssize_t
    ok = send_timeout(
        wintypes.HWND(hwnd),
        wintypes.UINT(message),
        wintypes.WPARAM(w_param),
        wintypes.LPARAM(l_param),
        0x0002,  # SMTO_ABORTIFHUNG
        max(1, timeout_ms),
        ctypes.byref(result),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if not ok:
        error = ctypes.get_last_error()
        return {'ok': False, 'return': None, 'error': f'error={error}', 'elapsed_ms': elapsed_ms}
    return {'ok': True, 'return': result.value, 'error': None, 'elapsed_ms': elapsed_ms}


KEYS = {
    'esc': 0x1B,
    'enter': 0x0D,
    'space': 0x20,
    'tab': 0x09,
    'left': 0x25,
    'up': 0x26,
    'right': 0x27,
    'down': 0x28,
    'f1': 0x70,
}
LETTER_KEYS = {chr(code): code for code in range(ord('A'), ord('Z') + 1)}
EXTENDED_KEYS = {0x25, 0x26, 0x27, 0x28}


def key_lparam(vk: int, key_up: bool = False) -> int:
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    value = 1 | (scan << 16)
    if vk in EXTENDED_KEYS:
        value |= 1 << 24
    if key_up:
        value |= (1 << 30) | (1 << 31)
    return value


def build_cases(
    point: tuple[int, int],
    screen_point: tuple[int, int],
) -> list[dict]:
    x, y = point
    l_param = win32api.MAKELONG(x, y)
    screen_l_param = win32api.MAKELONG(*screen_point)
    cases = [
        {'name': 'activate', 'message': win32con.WM_ACTIVATE, 'w_param': win32con.WA_ACTIVE, 'l_param': 0},
        {'name': 'mouse_move', 'message': win32con.WM_MOUSEMOVE, 'w_param': 0, 'l_param': l_param},
        {
            'name': 'mouse_move_left_button',
            'message': win32con.WM_MOUSEMOVE,
            'w_param': win32con.MK_LBUTTON,
            'l_param': l_param,
        },
        {
            'name': 'mouse_wheel_up_client_lparam',
            'message': win32con.WM_MOUSEWHEEL,
            'w_param': win32api.MAKELONG(0, win32con.WHEEL_DELTA),
            'l_param': l_param,
        },
        {
            'name': 'mouse_wheel_down_client_lparam',
            'message': win32con.WM_MOUSEWHEEL,
            'w_param': win32api.MAKELONG(0, -win32con.WHEEL_DELTA),
            'l_param': l_param,
        },
        {
            'name': 'mouse_wheel_up_screen_lparam',
            'message': win32con.WM_MOUSEWHEEL,
            'w_param': win32api.MAKELONG(0, win32con.WHEEL_DELTA),
            'l_param': screen_l_param,
        },
        {
            'name': 'mouse_wheel_down_screen_lparam',
            'message': win32con.WM_MOUSEWHEEL,
            'w_param': win32api.MAKELONG(0, -win32con.WHEEL_DELTA),
            'l_param': screen_l_param,
        },
    ]
    cases.extend(
        [
            {'name': 'left_button_down', 'message': win32con.WM_LBUTTONDOWN, 'w_param': win32con.MK_LBUTTON, 'l_param': l_param},
            {'name': 'left_button_up', 'message': win32con.WM_LBUTTONUP, 'w_param': 0, 'l_param': l_param},
            {'name': 'right_button_down', 'message': win32con.WM_RBUTTONDOWN, 'w_param': win32con.MK_RBUTTON, 'l_param': l_param},
            {'name': 'right_button_up', 'message': win32con.WM_RBUTTONUP, 'w_param': 0, 'l_param': l_param},
            {'name': 'middle_button_down', 'message': win32con.WM_MBUTTONDOWN, 'w_param': win32con.MK_MBUTTON, 'l_param': l_param},
            {'name': 'middle_button_up', 'message': win32con.WM_MBUTTONUP, 'w_param': 0, 'l_param': l_param},
        ]
    )
    for key_name, vk in {**KEYS, **LETTER_KEYS}.items():
        cases.extend(
            [
                {
                    'name': f'key_down_{key_name.lower()}',
                    'message': win32con.WM_KEYDOWN,
                    'w_param': vk,
                    'l_param': key_lparam(vk),
                    'key_name': key_name,
                },
                {
                    'name': f'key_up_{key_name.lower()}',
                    'message': win32con.WM_KEYUP,
                    'w_param': vk,
                    'l_param': key_lparam(vk, key_up=True),
                    'key_name': key_name,
                },
            ]
        )
    return cases


def run_case(
    hwnd: int,
    output_dir: str,
    case: dict,
    target_hwnd: int,
    mode: str,
    timeout_ms: int,
    settle_seconds: float,
) -> dict:
    name = case['name']
    before_path = os.path.join(output_dir, f'{name}_00_before.png')
    before = capture_printwindow(hwnd, before_path)
    if mode == 'post':
        dispatch = post_message(target_hwnd, case['message'], case['w_param'], case['l_param'])
    elif mode == 'send':
        dispatch = send_message(target_hwnd, case['message'], case['w_param'], case['l_param'], timeout_ms)
    else:
        dispatch = {
            'post': post_message(target_hwnd, case['message'], case['w_param'], case['l_param']),
            'send': send_message(target_hwnd, case['message'], case['w_param'], case['l_param'], timeout_ms),
        }
    time.sleep(settle_seconds)
    after_path = os.path.join(output_dir, f'{name}_01_after.png')
    after = capture_printwindow(hwnd, after_path)
    result = {
        **case,
        'message_name': message_name(case['message']),
        'target_hwnd': target_hwnd,
        'before_screenshot': before_path,
        'after_screenshot': after_path,
        'pixel_delta_mean': float(np.abs(after.astype(np.int16) - before.astype(np.int16)).mean()),
        'dispatch': dispatch,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', default='', help='Directory for screenshots and result.json')
    parser.add_argument('--game-path', default='', help='Optional exact path to nikke.exe')
    parser.add_argument('--x', type=int, default=505, help='Client X coordinate')
    parser.add_argument('--y', type=int, default=1000, help='Client Y coordinate')
    parser.add_argument('--mode', choices=('post', 'send', 'both'), default='post')
    parser.add_argument('--send-timeout-ms', type=int, default=500)
    parser.add_argument('--settle-seconds', type=float, default=0.5)
    args = parser.parse_args()
    if not is_admin():
        raise SystemExit('Run from an elevated PowerShell so PrintWindow can capture NIKKE.')
    if not 0 <= args.x < REFERENCE_SIZE[0] or not 0 <= args.y < REFERENCE_SIZE[1]:
        raise SystemExit(f'Point must be inside client size {REFERENCE_SIZE}.')
    dpi_awareness = enable_dpi_awareness()
    output_dir = args.output_dir or os.path.join(os.getcwd(), 'tmp', 'nikke-message-test')
    os.makedirs(output_dir, exist_ok=True)
    hwnd = find_game_window(args.game_path)
    _, _, width, height = client_geometry(hwnd)
    if (width, height) != REFERENCE_SIZE:
        raise SystemExit(f'Expected a 720x1280 client, got {width}x{height}.')
    target_hwnd, screen_point, local_point = target_client_point(hwnd, (args.x, args.y))
    cases = build_cases((args.x, args.y), screen_point)
    result = {
        'timestamp': datetime.now().isoformat(),
        'is_admin': is_admin(),
        'dpi_awareness': dpi_awareness,
        'hwnd': hwnd,
        'target_hwnd': target_hwnd,
        'process_path': process_path(hwnd),
        'client_size': [width, height],
        'client_point': [args.x, args.y],
        'screen_point': list(screen_point),
        'target_local_point': list(local_point),
        'mode': args.mode,
        'initial_foreground': win32gui.GetForegroundWindow(),
        'cases': [],
    }
    for case in cases:
        result['cases'].append(
            run_case(hwnd, output_dir, case, target_hwnd, args.mode, args.send_timeout_ms, args.settle_seconds)
        )
    result['final_foreground'] = win32gui.GetForegroundWindow()
    result_path = os.path.join(output_dir, 'result.json')
    with open(result_path, 'w', encoding='utf-8') as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
    print(f'Saved screenshots and result to: {result_path}')


if __name__ == '__main__':
    main()
