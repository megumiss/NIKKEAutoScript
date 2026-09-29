"""Reconstruct NIKKE's expanded minimap from driver-controlled overlapping views.

Run ``python dev_tools/minimap_reconstruct.py --help`` for capture/rebuild options.
The scanner never treats an empty grid as a confirmed camera clamp.
"""

import argparse
import copy
import ctypes
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import cv2
import numpy as np
from PIL import Image


def stop_requested(args, output):
    sentinel = getattr(args, 'stop_file', None)
    return (output / 'STOP').exists() or (sentinel is not None and sentinel.exists())


def terrain(image):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (85, 65, 125), (115, 255, 255))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    result = np.zeros(mask.shape, np.uint8)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= 90:
            result[labels == index] = 255
    return cv2.morphologyEx(result, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))


def grid_horizon(image):
    """Fit the two grid-line vanishing points, away from terrain and controls."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (90, 80, 95), (115, 255, 240))
    mask[cv2.dilate(terrain(image), np.ones((21, 21), np.uint8)) > 0] = 0
    mask[-35:, -75:] = 0
    segments = cv2.HoughLinesP(mask, 1, np.pi / 720, 35, minLineLength=85, maxLineGap=15)
    if segments is None:
        raise ValueError('Not enough background grid; supply --horizon from a saved calibration.')
    vanishing = []
    residuals = []
    for sign in (-1, 1):
        lines = []
        for x1, y1, x2, y2 in segments.reshape(-1, 4):
            if x2 == x1 or not 0.35 < sign * (y2 - y1) / (x2 - x1) < 2:
                continue
            line = np.cross([x1, y1, 1.0], [x2, y2, 1.0])
            lines.append(line / np.linalg.norm(line[:2]))
        if len(lines) < 4:
            raise ValueError('Insufficient grid directions; supply --horizon.')
        lines = np.array(lines)
        for _ in range(3):
            point = np.linalg.lstsq(lines[:, :2], -lines[:, 2], rcond=None)[0]
            errors = np.abs(lines @ np.r_[point, 1])
            keep = errors <= max(4, float(np.median(errors)) * 2.5)
            if keep.sum() < 4:
                break
            lines = lines[keep]
        vanishing.append(np.r_[point, 1])
        residuals.append(float(np.median(errors)))
    horizon = np.cross(*vanishing)
    horizon /= horizon[2]
    y = -1 / horizon[1]
    if not -2000 < y < -100 or max(residuals) > 8 or abs(horizon[0]) > 0.00015:
        raise ValueError(f'Unreliable grid calibration: {y=}, {residuals=}')
    return float(y), {'vanishing_points': [v.tolist() for v in vanishing], 'residuals': residuals}


class Projection:
    def __init__(self, shape, horizon):
        height, width = shape
        self.size = (width, height)
        self.matrix = np.array([[1, width / (2 * -horizon), 0], [0, 1, 0], [0, 1 / -horizon, 1.0]])
        source = np.full(shape, 255, np.uint8)
        source[:3] = source[-3:] = 0
        source[:, :3] = source[:, -3:] = 0
        source[-35:, -75:] = 0
        self.valid = cv2.warpPerspective(source, self.matrix, self.size, flags=cv2.INTER_NEAREST)

    def mask(self, image):
        return cv2.warpPerspective(terrain(image), self.matrix, self.size, flags=cv2.INTER_NEAREST)


def overlap(first, second, valid, shift):
    matrix = np.float32([[1, 0, -shift[0]], [0, 1, -shift[1]]])
    size = first.shape[::-1]
    moved = cv2.warpAffine(second, matrix, size, flags=cv2.INTER_NEAREST)
    common = (valid > 0) & (cv2.warpAffine(valid, matrix, size, flags=cv2.INTER_NEAREST) > 0)
    a, b = first > 0, moved > 0
    union = int(((a | b) & common).sum())
    return float((a & b & common).sum() / max(1, union)), union


def register(first, second, valid, prediction=(0, 0)):
    """Translation after grid-plane rectification; reject texture-only matches."""
    stationary_iou, stationary_area = overlap(first, second, valid, (0, 0))
    if stationary_iou >= 0.995 and stationary_area >= 80:
        return {'delta': [0.0, 0.0], 'iou': stationary_iou, 'area': stationary_area}
    if min(np.count_nonzero(first), np.count_nonzero(second)) < 150:
        return None
    a = cv2.GaussianBlur(first.astype(np.float32) / 255, (7, 7), 0)
    b = cv2.GaussianBlur(second.astype(np.float32) / 255, (7, 7), 0)
    phase, _ = cv2.phaseCorrelate(a, b)
    seeds = [prediction, phase, (0, 0)]
    best = None
    for seed in seeds:
        if max(abs(float(value)) for value in seed) > max(first.shape) * 0.65:
            continue
        matrix = np.float32([[1, 0, seed[0]], [0, 1, seed[1]]])
        try:
            _, matrix = cv2.findTransformECC(
                a, b, matrix, cv2.MOTION_TRANSLATION,
                (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 100, 1e-5), valid, 5,
            )
        except cv2.error:
            continue
        delta = matrix[:, 2].astype(float)
        score, area = overlap(first, second, valid, delta)
        if area < 80 or np.linalg.norm(delta) > max(first.shape) * 0.65:
            continue
        if best is None or score > best['iou']:
            best = {'delta': delta.tolist(), 'iou': score, 'area': area}
    return best if best and best['iou'] >= 0.86 else None


class DriverWindow:
    def __init__(self, args):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        sys.path.insert(0, str(args.driver_root.resolve()))
        import win32gui

        self.gui = win32gui
        self.capture_dc = None
        self.hwnd = win32gui.FindWindow('UnityWndClass', 'NIKKE')
        if not self.hwnd:
            raise RuntimeError('Open NIKKE and expand the minimap before capture.')
        self.roi = tuple(args.roi)
        self.args = args
        self.ensure_client_size()
        working_directory = Path.cwd()
        try:
            from module.device.win.virtual_mouse.input import VirtualMouseInput, release_driver_control
        finally:
            os.chdir(working_directory)
        self.release = release_driver_control
        self.handler = VirtualMouseInput(config_name='minimap_reconstruct', move_backend='driver')

    def ensure_client_size(self):
        import pywintypes
        import win32api
        import win32con

        target = (0, 0, *self.args.client)
        before = self.gui.GetClientRect(self.hwnd)
        try:
            if (self.gui.IsIconic(self.hwnd)
                    or self.gui.GetWindowPlacement(self.hwnd)[1] == win32con.SW_SHOWMAXIMIZED):
                self.gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                time.sleep(0.5)
            for attempt in range(3):
                client = self.gui.GetClientRect(self.hwnd)
                left, top, right, bottom = self.gui.GetWindowRect(self.hwnd)
                width = right - left - client[2] + target[2]
                height = bottom - top - client[3] + target[3]
                monitor = win32api.MonitorFromWindow(self.hwnd, win32con.MONITOR_DEFAULTTONEAREST)
                wl, wt, wr, wb = win32api.GetMonitorInfo(monitor)['Work']
                if width > wr - wl or height > wb - wt:
                    raise RuntimeError(f'Client {self.args.client} with window borders '
                                       'does not fit the monitor work area.')
                x, y = max(wl, min(left, wr - width)), max(wt, min(top, wb - height))
                if client != target or (x, y) != (left, top):
                    print(json.dumps({'client_resize': 'setting', 'from': client[2:],
                                      'to': target[2:], 'attempt': attempt + 1}), flush=True)
                    self.gui.SetWindowPos(self.hwnd, 0, x, y, width, height,
                                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
                # Unity may apply another size after WM_SIZE; require a full second of stable client geometry.
                for _ in range(4):
                    time.sleep(0.25)
                    if self.gui.GetClientRect(self.hwnd) != target:
                        break
                else:
                    print(json.dumps({'client_resize': 'ready', 'before': before[2:], 'client': target[2:]}),
                          flush=True)
                    return
        except pywintypes.error as exc:
            raise RuntimeError(f'Cannot set game client to {self.args.client}: {exc}. '
                               'Run the capture script with the same permissions as NIKKE '
                               '(as administrator if the game is elevated).') from exc
        actual = self.gui.GetClientRect(self.hwnd)
        raise RuntimeError(f'Game client did not stabilize at {self.args.client}; observed {actual[2:]}.')

    def check(self):
        if self.gui.GetClientRect(self.hwnd) != (0, 0, *self.args.client):
            raise RuntimeError(f'Client size changed; expected {self.args.client}. Capture stopped.')

    @staticmethod
    def map_visible(image):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        return ((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 115) & (hsv[:, :, 1] > 40)).mean() >= 0.45

    def capture(self, require_map=True):
        import win32con
        import win32ui

        self.check()
        if self.gui.GetForegroundWindow() != self.hwnd:
            raise RuntimeError('Game lost focus; capture stopped before accepting another window as map data.')
        x, y = self.gui.ClientToScreen(self.hwnd, (0, 0))
        left, top, right, bottom = self.roi
        width, height = right - left, bottom - top
        if self.capture_dc is None:
            self.screen_handle = self.gui.GetDC(0)
            self.screen_dc = win32ui.CreateDCFromHandle(self.screen_handle)
            self.capture_dc = self.screen_dc.CreateCompatibleDC()
            self.capture_bitmap = win32ui.CreateBitmap()
            self.capture_bitmap.CreateCompatibleBitmap(self.screen_dc, width, height)
            self.capture_dc.SelectObject(self.capture_bitmap)
        self.capture_dc.BitBlt((0, 0), (width, height), self.screen_dc, (x + left, y + top), win32con.SRCCOPY)
        image = np.frombuffer(self.capture_bitmap.GetBitmapBits(True), np.uint8).reshape(height, width, 4)
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        if require_map and not self.map_visible(image):
            raise RuntimeError('Expanded minimap no longer visible or is covered. Capture stopped.')
        return image

    def reset_minimap(self, expanded=True):
        from PIL import ImageGrab

        self.focus()
        origin = self.gui.ClientToScreen(self.hwnd, (0, 0))
        x, y = self.args.map_open

        def state():
            if self.map_visible(self.capture(require_map=False)):
                return 'expanded'
            panel = ImageGrab.grab(bbox=(origin[0] + x - 22, origin[1] + y - 22,
                                       origin[0] + x + 186, origin[1] + y + 196), all_screens=True)
            panel = cv2.cvtColor(np.array(panel), cv2.COLOR_RGB2BGR)
            if self.map_visible(panel[42:197, 7:187]):
                return 'compact'
            icon = cv2.cvtColor(panel[:44, :44], cv2.COLOR_BGR2HSV)
            white = cv2.inRange(icon, (0, 0, 185), (179, 70, 255))
            if 150 <= np.count_nonzero(white) <= 550 and (icon[:, :, 2] < 130).mean() > 0.5:
                return 'closed'
            return 'unknown'

        def click(point):
            self.focus()
            self.handler.mouse_click(origin[0] + point[0], origin[1] + point[1])
            if self.handler._failures:
                raise RuntimeError('Driver failed to toggle the minimap; scan not started.')

        def wait_for(expected):
            deadline = time.monotonic() + 3
            # The panel becomes visible before its opening animation accepts another click.
            time.sleep(1.2)
            current = 'unknown'
            while time.monotonic() < deadline:
                current = state()
                if current == expected:
                    return
                time.sleep(0.1)
            raise RuntimeError(f'Minimap did not reach {expected} (observed {current}); scan not started.')

        current = state()
        if current == 'unknown':
            raise RuntimeError('Minimap controls are not visible; no toggle click sent.')
        if current == 'expanded':
            click((self.roi[0] + 14, self.roi[1] - 11))
            wait_for('compact')
        elif current == 'closed':
            click((x, y))
            wait_for('compact')
        if expanded:
            click((x, y))
            wait_for('expanded')
            time.sleep(0.5)
        print(json.dumps({'minimap_reset': 'expanded' if expanded else 'compact'}), flush=True)

    def focus(self):
        import pywintypes

        self.check()
        if self.gui.GetForegroundWindow() == self.hwnd:
            return
        origin = self.gui.ClientToScreen(self.hwnd, (0, 0))
        try:
            self.gui.SetForegroundWindow(self.hwnd)
        except pywintypes.error:
            point = (origin[0] + 400, origin[1] - 20)
            if self.gui.GetAncestor(self.gui.WindowFromPoint(point), 2) != self.hwnd:
                raise RuntimeError('Game title bar is covered; no focus click sent.')
            self.handler.mouse_click(*point)
        time.sleep(0.25)
        if self.gui.GetForegroundWindow() != self.hwnd:
            raise RuntimeError('Cannot focus the game; no drag sent.')

    def drag_vector(self, sx, sy):
        """Swipe along an arbitrary screen vector; the camera moves opposite to the content."""
        self.focus()
        origin = self.gui.ClientToScreen(self.hwnd, (0, 0))
        left, top, right, bottom = self.roi
        center = ((left + right) // 2, (top + bottom) // 2)
        p1 = (round(center[0] - sx / 2), round(center[1] - sy / 2))
        p2 = (round(center[0] + sx / 2), round(center[1] + sy / 2))
        for x, y in (p1, p2):
            if not left + 20 <= x <= right - 20 or not top + 20 <= y <= bottom - 20:
                raise ValueError('Drag would leave the safe minimap interior; reduce the swipe length.')
        self.handler.mouse_swipe(
            (origin[0] + p1[0], origin[1] + p1[1]), (origin[0] + p2[0], origin[1] + p2[1]),
        )
        if self.handler._failures:
            raise RuntimeError('Driver reported a failed gesture.')
        time.sleep(self.args.settle)

    def drag(self, direction):
        self.focus()
        self.capture()
        origin = self.gui.ClientToScreen(self.hwnd, (0, 0))
        left, top, right, bottom = self.roi
        center = ((left + right) // 2, (top + bottom) // 2)
        # Swapping the exact endpoints makes return gestures invert the same perspective displacement.
        dx, dy = direction
        half = self.args.step // 2
        p1 = (round(center[0] - dx * half), round(center[1] - dy * half))
        p2 = (round(center[0] + dx * half), round(center[1] + dy * half))
        for x, y in (p1, p2):
            if not left + 20 <= x <= right - 20 or not top + 20 <= y <= bottom - 20:
                raise ValueError('Drag would leave the safe minimap interior; reduce --step.')
        self.handler.mouse_swipe(
            (origin[0] + p1[0], origin[1] + p1[1]), (origin[0] + p2[0], origin[1] + p2[1]),
        )
        if self.handler._failures:
            raise RuntimeError('Driver reported a failed gesture.')
        time.sleep(self.args.settle)

    def close(self):
        try:
            self.handler.mouse_up()
        finally:
            try:
                if self.capture_dc is not None:
                    self.capture_dc.DeleteDC()
                    self.screen_dc.DeleteDC()
                    self.gui.ReleaseDC(0, self.screen_handle)
                    self.gui.DeleteObject(self.capture_bitmap.GetHandle())
                    self.capture_dc = None
            finally:
                self.release()


class Scanner:
    DIRECTIONS = ((0, -1), (-1, 0), (0, 1), (1, 0))

    def __init__(self, args, window):
        self.args, self.window = args, window
        self.output = args.output
        self.output.mkdir(parents=True, exist_ok=True)
        if (self.output / 'scan.json').exists():
            raise ValueError('Output already contains a scan; use a new directory or --rebuild.')
        first = window.capture()
        if args.horizon is None:
            horizon, calibration = grid_horizon(first)
        else:
            horizon, calibration = args.horizon, {'source': 'explicit'}
        self.projection = Projection(first.shape[:2], horizon)
        self.data = {
            'version': 1, 'roi': args.roi, 'client': args.client, 'horizon': horizon,
            'calibration': calibration, 'step': args.step, 'axis_slope': args.axis_slope,
            'frames': [], 'edges': [], 'clamps': [], 'status': 'running',
            'coverage_definition': 'serpentine sweep between verified camera clamps along ground-grid axes',
        }
        self.current_mask = None
        self.position = np.zeros(2)
        self.last_id = None
        self.motion = {}
        self.last_visible = None
        self.add(first, (0, 0), 'start')

    def relocalize(self, exclude_id):
        """Fringe views are weak anchors; try any other accepted frame near the predicted pose."""
        if np.count_nonzero(self.current_mask) < 150:
            return None
        best = None
        for frame in self.data['frames']:
            if frame['id'] in (exclude_id, self.last_id) or frame['terrain_pixels'] < 600:
                continue
            prediction = np.asarray(frame['position']) - self.position
            if np.linalg.norm(prediction) > 350:
                continue
            image = cv2.imread(str(self.output / frame['file']))
            match = register(self.projection.mask(image), self.current_mask, self.projection.valid, prediction)
            if match and match['iou'] >= 0.92 and (best is None or match['iou'] > best[2]):
                best = (frame, np.asarray(match['delta']), match['iou'])
        return best

    def return_to(self, anchor, direction):
        """Close each excursion using terrain, since edge sliding makes inverse gestures insufficient."""
        anchor_image = cv2.imread(str(self.output / anchor['file']))
        anchor_mask = self.projection.mask(anchor_image)
        last_direction = np.asarray(direction, float)
        for attempt in range(12):
            prediction = np.asarray(anchor['position']) - self.position
            match = register(anchor_mask, self.current_mask, self.projection.valid, prediction)
            if match and match['iou'] >= 0.90:
                delta = np.asarray(match['delta'])
                self.position = np.asarray(anchor['position']) - delta
                self.data['frames'][-1]['position'] = self.position.tolist()
                self.data['edges'].append({'a': anchor['id'], 'b': self.last_id,
                                          'delta': (-delta).tolist(), 'iou': match['iou'], 'kind': 'return'})
                # The game's drag dead zone prevents tiny corrections; retain the measured residual pose.
                if np.linalg.norm(delta) < 12:
                    self.save()
                    return
                command = -delta / (np.array([0.39, 0.21]) * self.args.step)
                command /= max(1, float(np.max(np.abs(command))))
            else:
                fallback = self.relocalize(anchor['id'])
                if fallback is not None:
                    frame, delta, iou = fallback
                    self.position = np.asarray(frame['position']) - delta
                    self.data['frames'][-1]['position'] = self.position.tolist()
                    self.data['edges'].append({'a': frame['id'], 'b': self.last_id,
                                               'delta': (-delta).tolist(), 'iou': iou, 'kind': 'return_relocalize'})
                    residual = np.asarray(anchor['position']) - self.position
                    if np.linalg.norm(residual) < 12:
                        self.save()
                        return
                    command = -residual / (np.array([0.39, 0.21]) * self.args.step)
                    command /= max(1, float(np.max(np.abs(command))))
                elif attempt == 0 or np.count_nonzero(self.current_mask) < 150:
                    command = -last_direction
                else:
                    raise RuntimeError('Return landmark lost; scan stopped without inventing a camera position.')
            last_direction = command
            self.move(tuple(command), anchor['tile'], 'visual_return')
        raise RuntimeError('Could not visually return to the parent view within twelve corrections.')

    def explore_terrain(self):
        """Explore visible terrain with verified returns; an empty halo is not a world boundary."""
        self.data['coverage_definition'] = 'visually connected terrain views; empty areas do not prove chapter limits'
        self.data['unresolved_frontiers'] = []
        accepted = [self.data['frames'][0]]
        stack = [{'frame': accepted[0], 'next': 0, 'direction': None}]
        self.data['accepted_frames'] = [0]
        height, width = self.current_mask.shape

        def new_area(position):
            unseen = self.projection.valid.copy()
            for old in accepted:
                shift = np.asarray(old['position']) - position
                if abs(shift[0]) >= width or abs(shift[1]) >= height:
                    continue
                matrix = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
                seen = cv2.warpAffine(self.projection.valid, matrix, (width, height), flags=cv2.INTER_NEAREST)
                unseen[seen > 0] = 0
            return int(np.count_nonzero(unseen))

        while stack:
            node = stack[-1]
            if node['next'] == len(self.DIRECTIONS):
                stack.pop()
                if stack:
                    self.return_to(stack[-1]['frame'], node['direction'])
                continue
            direction = self.DIRECTIONS[node['next']]
            node['next'] += 1
            if new_area(self.position - self.prediction(direction)) < 1800:
                continue
            parent = self.data['frames'][-1]
            record = self.move(direction, (len(stack), node['next']), 'terrain_frontier')
            match = record['registration']
            if match is None:
                self.data['unresolved_frontiers'].append({'frame': record['id'], 'reason': 'blank_or_unregistered'})
            elif np.linalg.norm(match['delta']) < 3:
                confirmation = self.move(direction, record['tile'], 'terrain_clamp_check')
                second = confirmation['registration']
                if second and np.linalg.norm(second['delta']) < 3:
                    self.data['clamps'].append({'direction': direction, 'frames': [record['id'], confirmation['id']]})
                else:
                    self.data['unresolved_frontiers'].append({'frame': confirmation['id'], 'reason': 'unconfirmed_clamp'})
            elif new_area(self.position) >= 1800 and record['terrain_pixels'] >= 150:
                accepted.append(record)
                self.data['accepted_frames'].append(record['id'])
                stack.append({'frame': record, 'next': 0, 'direction': direction})
                continue
            self.return_to(parent, direction)
        self.data['status'] = 'connected_terrain_explored'
        self.save()

    def save(self):
        temporary = self.output / 'scan.json.tmp'
        temporary.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        # Defender/indexer can briefly lock the target; os.replace fails with WinError 5 then.
        for _ in range(10):
            try:
                temporary.replace(self.output / 'scan.json')
                return
            except PermissionError:
                time.sleep(0.3)
        temporary.replace(self.output / 'scan.json')

    def add(self, image, tile, kind, registration=None, direction=None):
        index = len(self.data['frames'])
        name = f'frame_{index:05d}.png'
        if not cv2.imwrite(str(self.output / name), image):
            raise OSError(f'Could not save {name}')
        mask = self.projection.mask(image)
        if registration is not None:
            delta = np.array(registration['delta'])
            self.position -= delta
            self.data['edges'].append({
                'a': self.last_id, 'b': index, 'delta': (-delta).tolist(), 'iou': registration['iou'],
            })
        elif direction is not None:
            delta = self.prediction(direction)
            self.position -= delta
            self.data['edges'].append({'a': self.last_id, 'b': index, 'delta': (-delta).tolist(), 'iou': None})
        record = {
            'id': index, 'file': name, 'tile': list(tile), 'kind': kind,
            'position': self.position.tolist(), 'terrain_pixels': int(np.count_nonzero(mask)),
            'registration': registration,
        }
        self.data['frames'].append(record)
        if np.count_nonzero(mask) >= 150:
            if self.last_visible is not None and self.last_visible[0] != self.last_id:
                previous, previous_mask = self.last_visible
                prediction = np.array(self.data['frames'][previous]['position']) - self.position
                match = register(previous_mask, mask, self.projection.valid, prediction)
                if match and match['iou'] > 0.94:
                    self.data['edges'].append({
                        'a': previous, 'b': index, 'delta': (-np.array(match['delta'])).tolist(),
                        'iou': match['iou'], 'kind': 'visible_return',
                    })
                    self.position = np.array(self.data['frames'][previous]['position']) - match['delta']
                    record['position'] = self.position.tolist()
            self.last_visible = index, mask.copy()
        self.current_mask, self.last_id = mask, index
        self.save()
        print(json.dumps({k: record[k] for k in ('id', 'tile', 'kind', 'terrain_pixels')}, ensure_ascii=False), flush=True)
        return record

    def prediction(self, direction):
        key = tuple(direction)
        if key in self.motion:
            return np.median(self.motion[key], axis=0)
        reverse = tuple(-value for value in direction)
        if reverse in self.motion:
            return -np.median(self.motion[reverse], axis=0)
        return np.array(direction, float) * np.array([0.34, 0.21]) * self.args.step

    def move(self, direction, tile, kind):
        if stop_requested(self.args, self.output):
            raise RuntimeError('Stopped by STOP file; scan remains incomplete.')
        if len(self.data['frames']) >= self.args.max_frames:
            raise RuntimeError('Frame budget reached; scan is incomplete, not a map boundary.')
        before = self.current_mask
        self.window.drag(direction)
        image = self.window.capture()
        after = self.projection.mask(image)
        match = register(before, after, self.projection.valid, self.prediction(direction))
        if match and 5 < np.linalg.norm(match['delta']) < 180:
            self.motion.setdefault(tuple(direction), []).append(match['delta'])
        return self.add(image, tile, kind, match, direction)

    def axis_direction(self, axis, sign):
        height = self.projection.size[1]
        raw_slope = self.args.axis_slope * (1 + height / (2 * -self.data['horizon']))
        return (float(sign if axis == 0 else -sign), float(sign * raw_slope))

    def axis_progress(self, match, axis):
        if match is None:
            return None
        dx, dy = match['delta']
        return abs(((dx if axis == 0 else -dx) + dy / self.args.axis_slope) / 2)

    def seek_edge(self, axis, sign, row, phase):
        direction = self.axis_direction(axis, sign)
        still = []
        blanks = 0
        for column in range(self.args.max_axis_steps):
            record = self.move(direction, (row, column), phase)
            progress = self.axis_progress(record['registration'], axis)
            if progress is not None and progress < 4 and record['registration']['iou'] > 0.90:
                still.append(record['id'])
            else:
                still.clear()
            blanks = blanks + 1 if record['terrain_pixels'] < 150 else 0
            if blanks >= 2:
                if self.probe_blank_edge(axis, sign, row):
                    return
                blanks = 0
            if len(still) >= 2:
                self.data['clamps'].append({'axis': axis, 'sign': sign, 'row': row, 'frames': still[-2:]})
                self.save()
                return
        raise RuntimeError(f'Axis {axis}, sign {sign}: no verifiable boundary after {self.args.max_axis_steps} steps.')

    def probe_blank_edge(self, axis, sign, row):
        """Observe an empty limit indirectly by returning to the same visible landmark.

        From a blank view, retreat n gestures to an anchor. Advance n+2 and
        retreat n again. A saturated normal component returns to the anchor;
        unrestricted camera motion exposes a different view. Grid pixels alone
        are never evidence that the camera stopped.
        """
        outward = self.axis_direction(axis, sign)
        inward = self.axis_direction(axis, -sign)
        start = self.last_id
        for distance in range(1, 9):
            anchor = self.move(inward, (row, distance), 'blank_probe_inward')
            if anchor['terrain_pixels'] >= 600:
                break
        else:
            raise RuntimeError('No visible anchor within eight inward gestures; boundary remains unknown.')
        anchor_mask = self.current_mask.copy()
        for _ in range(distance + 2):
            self.move(outward, (row, distance), 'blank_probe_outward')
        for _ in range(distance):
            returned = self.move(inward, (row, distance), 'blank_probe_return')
        match = register(anchor_mask, self.current_mask, self.projection.valid)
        progress = self.axis_progress(match, axis)
        confirmed = match is not None and match['iou'] > 0.90 and progress < 6
        evidence = {'axis': axis, 'sign': sign, 'row': row, 'start': start,
                    'frames': [anchor['id'], returned['id']], 'retreat_steps': distance,
                    'registration': match, 'confirmed': confirmed, 'kind': 'landmark_return'}
        self.data.setdefault('boundary_probes', []).append(evidence)
        if match:
            self.data['edges'].append({'a': anchor['id'], 'b': returned['id'],
                                       'delta': (-np.array(match['delta'])).tolist(),
                                       'iou': match['iou'], 'kind': 'boundary_return'})
        for _ in range(distance):
            self.move(outward, (row, distance), 'blank_probe_restore')
        if confirmed:
            self.data['clamps'].append(evidence)
        self.save()
        print(json.dumps({'boundary_probe': evidence}), flush=True)
        return confirmed

    def explore(self):
        # Camera limits follow the ground grid, not screen x/y. A zero normal component is a clamp
        # even when the other component slides along that edge.
        self.seek_edge(0, 1, -1, 'seek_corner_a')
        self.seek_edge(1, 1, -1, 'seek_corner_b')
        self.seek_edge(0, 1, -1, 'confirm_corner_a')
        row = 0
        sign = -1
        while True:
            self.seek_edge(0, sign, row, 'sweep')
            record = self.move(self.axis_direction(1, -1), (row + 1, 0), 'next_row')
            progress = self.axis_progress(record['registration'], 1)
            if record['terrain_pixels'] < 150 and self.probe_blank_edge(1, -1, row):
                break
            if progress is not None and progress < 1.5 and record['registration']['iou'] > 0.95:
                confirmation = self.move(self.axis_direction(1, -1), (row + 1, 0), 'end_boundary_check')
                second = self.axis_progress(confirmation['registration'], 1)
                if second is not None and second < 1.5 and confirmation['registration']['iou'] > 0.95:
                    self.data['clamps'].append({'axis': 1, 'sign': -1, 'row': row,
                                               'frames': [record['id'], confirmation['id']]})
                    break
            row += 1
            sign *= -1
        self.data['status'] = 'raster_closed'
        self.data['rows'] = row + 1
        self.save()


def terrain_crop_bounds(probability, margin=48):
    height, width = probability.shape
    ys, xs = np.nonzero(probability > 0.15)
    margin = max(0, int(np.ceil(margin)))
    if len(xs):
        # Include faint seams and every disconnected road, not just the largest component.
        box = [max(0, int(xs.min()) - margin), max(0, int(ys.min()) - margin),
               min(width, int(xs.max()) + 1 + margin), min(height, int(ys.max()) + 1 + margin)]
    else:
        box = [0, 0, width, height]
    return {'source_size': [width, height], 'box': box, 'margin_px': margin,
            'basis': 'terrain_probability > 0.15' if len(xs) else 'no_terrain_keep_canvas'}


def rebuild(output, registration='joint'):
    data = json.loads((output / 'scan.json').read_text(encoding='utf-8'))
    if data.get('version') == 2:
        rebuild_v2(output, data, registration=registration)
        return
    first = cv2.imread(str(output / data['frames'][0]['file']))
    projection = Projection(first.shape[:2], data['horizon'])
    positions = np.array([f['position'] for f in data['frames']])
    neighbours = {index: set() for index in range(len(positions))}
    for edge in data['edges']:
        if edge['iou'] is not None:
            neighbours[edge['a']].add(edge['b'])
            neighbours[edge['b']].add(edge['a'])
    localized, pending = {0}, [0]
    while pending:
        for index in neighbours[pending.pop()] - localized:
            localized.add(index)
            pending.append(index)
    # Sparse pose graph uses return observations to limit drift; blank frames carry low-weight odometry.
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import lsqr

    rows, cols, values, targets = [], [], [], []
    for row, edge in enumerate(data['edges']):
        weight = 1.0 if edge['iou'] is not None else 0.05
        rows.extend([row, row])
        cols.extend([edge['a'], edge['b']])
        values.extend([-weight, weight])
        targets.append(np.array(edge['delta']) * weight)
    row = len(targets)
    rows.append(row)
    cols.append(0)
    values.append(10.0)
    targets.append(np.zeros(2))
    matrix = coo_matrix((values, (rows, cols)), shape=(row + 1, len(positions))).tocsr()
    targets = np.array(targets)
    for axis in (0, 1):
        positions[:, axis] = lsqr(matrix, targets[:, axis], atol=1e-7, btol=1e-7, iter_lim=5000)[0]
    trusted_positions = positions[sorted(localized)]
    lo = np.floor(trusted_positions.min(axis=0)).astype(int) - 20
    hi = np.ceil(trusted_positions.max(axis=0)).astype(int) + projection.size + 20
    size = tuple(int(v) for v in hi - lo)
    if max(size) > 16000 or size[0] * size[1] > 60_000_000:
        raise RuntimeError(f'Implausible canvas {size}; inspect failed registrations instead of allocating it.')
    coverage = np.zeros(size[::-1], np.float32)
    roads = np.zeros_like(coverage)
    observed = np.zeros((*size[::-1], 3), np.uint8)
    best_weight = np.zeros_like(coverage)
    center_weight = cv2.distanceTransform(projection.valid, cv2.DIST_L2, 3)
    for frame, position in zip(data['frames'], positions):
        if frame['id'] not in localized:
            continue
        image = cv2.imread(str(output / frame['file']))
        mask = projection.mask(image).astype(np.float32) / 255
        transform = np.float32([[1, 0, position[0] - lo[0]], [0, 1, position[1] - lo[1]]])
        valid = cv2.warpAffine(projection.valid.astype(np.float32) / 255, transform, size)
        moved = cv2.warpAffine(mask, transform, size)
        coverage += valid
        roads += moved * valid
        color = cv2.warpPerspective(image, projection.matrix, projection.size)
        color = cv2.warpAffine(color, transform, size)
        weight = cv2.warpAffine(center_weight, transform, size)
        select = weight > best_weight
        observed[select] = color[select]
        best_weight[select] = weight[select]
    probability = roads / np.maximum(coverage, 1e-6)
    crop = terrain_crop_bounds(probability)
    left, top, right, bottom = crop['box']
    probability = probability[top:bottom, left:right]
    coverage = coverage[top:bottom, left:right]
    observed = observed[top:bottom, left:right]
    lo += (left, top)
    size = (right - left, bottom - top)
    canvas = np.full((*size[::-1], 3), (15, 20, 28), np.uint8)
    canvas[coverage > 0.5] = (30, 43, 55)
    canvas[(probability > 0.15) & (probability < 0.8)] = (86, 108, 128)
    canvas[probability >= 0.5] = (45, 141, 199)
    Image.fromarray(canvas).save(output / 'reconstruction.png')
    cv2.imwrite(str(output / 'observed_mosaic.png'), observed)
    cv2.imwrite(str(output / 'coverage.png'), np.uint8(np.minimum(coverage, 10) * 25.5))
    cv2.imwrite(str(output / 'terrain_mask.png'), np.uint8(probability >= 0.5) * 255)
    np.savez_compressed(output / 'map_data.npz', coverage=coverage, terrain_probability=probability,
                        positions=positions, origin=lo, projection=projection.matrix)
    summary = {
        'status': data['status'], 'frames': len(data['frames']), 'canvas_size': size,
        'crop': crop,
        'localized_frames': len(localized),
        'excluded_unlocalized_frames': sorted(set(range(len(positions))) - localized),
        'confirmed_clamps': data['clamps'],
        'unregistered_terrain_transitions': [f['id'] for f in data['frames'][1:]
                                            if f['terrain_pixels'] >= 150 and f['registration'] is None],
        'coverage_definition': data['coverage_definition'],
        'unresolved_frontiers': data.get('unresolved_frontiers', []),
        'whole_camera_domain_verified': False,
        'boundary_note': 'A finished traversal or empty view alone cannot prove full chapter coverage.',
    }
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


# ---------------------------------------------------------------------------
# v2: metric grid rectification + boundary-aware raster scan
# ---------------------------------------------------------------------------

def raw_grid(image):
    """Background grid lines, excluding terrain and the stage counter corner."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (90, 80, 95), (115, 255, 240))
    mask[cv2.dilate(terrain(image), np.ones((21, 21), np.uint8)) > 0] = 0
    mask[-35:, -75:] = 0
    return mask


def detect_markers(image, matrix):
    """Find fixed-size HUD symbols before the ground projection stretches them."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, (155, 60, 140), (179, 255, 255)) | cv2.inRange(hsv, (0, 60, 140), (8, 255, 255))
    white = cv2.inRange(hsv, (0, 0, 185), (179, 100, 255))
    white[cv2.dilate(red, np.ones((9, 9), np.uint8)) > 0] = 0
    for mask in (red, white):
        mask[:3] = mask[-3:] = 0
        mask[:, :3] = mask[:, -3:] = 0
        mask[-35:, -75:] = 0
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    enemies = []
    height, width = red.shape
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        # Clipped symbols bias the centroid and create duplicates near a view edge.
        if not (45 <= area <= 5000 and 10 <= w <= 120 and 10 <= h <= 120):
            continue
        if x <= 3 or y <= 3 or x + w >= width - 3 or y + h >= height - 3:
            continue
        if x + w >= width - 75 and y + h >= height - 35:
            continue
        moments = cv2.moments(contour)
        enemies.append([moments['m10'] / moments['m00'], moments['m01'] / moments['m00']])
    circles = cv2.HoughCircles(white, cv2.HOUGH_GRADIENT, 1, 40,
                               param1=100, param2=12, minRadius=12, maxRadius=32)
    players = []
    if circles is not None:
        for x, y, radius in circles[0]:
            if radius + 3 < x < width - radius - 3 and radius + 3 < y < height - radius - 3:
                players.append([float(x), float(y)])

    def project(points):
        if not points:
            return []
        return cv2.perspectiveTransform(np.array([points], np.float32), matrix)[0].round(1).tolist()

    return project(players), project(enemies)


class MetricGrid:
    """Metric rectification of the ground grid from its two vanishing points.

    Square grid cells make the two vanishing directions orthogonal in the
    world, which fixes the focal length; the rectified plane is then metric up
    to a uniform scale, so camera pans become pure translations here.
    """

    def __init__(self, image, cell_px=48.0):
        try:
            _, calibration = grid_horizon(image)
            v1, v2 = (np.array(point[:2]) for point in calibration['vanishing_points'])
        except ValueError:
            # Dim chapters only show the grid as sparse intersection dots.
            v1, v2 = self._vanishing_from_dots(image)
        height, width = image.shape[:2]
        center = np.array([width / 2, height / 2])
        focal2 = -float(np.dot(v1 - center, v2 - center))
        if focal2 < 900:
            raise ValueError(f'Degenerate grid geometry; cannot metric-rectify (f^2={focal2:.0f}).')
        focal = np.sqrt(focal2)
        intrinsic = np.array([[focal, 0, center[0]], [0, focal, center[1]], [0, 0, 1.0]])
        inverse = np.linalg.inv(intrinsic)
        r1 = inverse @ np.r_[v1, 1.0]
        r1 /= np.linalg.norm(r1)
        r2 = inverse @ np.r_[v2, 1.0]
        r2 /= np.linalg.norm(r2)
        homography = np.vstack([r1, r2, np.cross(r1, r2)]) @ inverse
        homography /= homography[2, 2]
        corners = np.array([[0, 0], [width, 0], [0, height], [width, height]], float)
        warped = cv2.perspectiveTransform(corners[None], homography)[0]
        # Two-pass: measure the rectified cell period, then normalize to cell_px.
        matrix, size = self._layout(homography, warped, 400.0)
        period = self._cell_period(image, matrix, size)
        self.matrix, self.size = self._layout(homography, warped, 400.0 * cell_px / float(np.mean(period)))
        self.cell = float(cell_px)
        source = np.full((height, width), 255, np.uint8)
        source[:3] = source[-3:] = 0
        source[:, :3] = source[:, -3:] = 0
        source[-35:, -75:] = 0
        self.valid = cv2.warpPerspective(source, self.matrix, self.size, flags=cv2.INTER_NEAREST)

    @staticmethod
    def _vanishing_from_dots(image):
        """Fit the two grid vanishing points from dot centroids when lines are too dim.

        Each dot votes for its nearest-neighbour directions; the two dominant
        direction families form line bundles that concur at the vanishing points.
        """
        from scipy.spatial import cKDTree

        mask = raw_grid(image)
        count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
        points = np.array([c for c, s in zip(centroids, stats[:, cv2.CC_STAT_AREA])
                           if 2 <= s <= 60])
        if len(points) < 30:
            raise ValueError('Too few grid dots for dot-based calibration; supply --horizon.')
        dists, neighbours = cKDTree(points).query(points, k=5)
        vectors = []
        for i in range(len(points)):
            for j in range(1, 5):
                if 15 < dists[i, j] < 60:
                    vectors.append((points[i], points[neighbours[i, j]] - points[i]))
        angles = np.degrees(np.arctan2([v[1][1] for v in vectors], [v[1][0] for v in vectors])) % 180
        hist, edges = np.histogram(angles, bins=72, range=(0, 180))
        first = int(np.argmax(hist))
        excluded = (np.arange(72) - first) % 72
        hist2 = hist.copy()
        hist2[(excluded < 10) | (excluded > 62)] = 0  # +-25 deg around the first family
        second = int(np.argmax(hist2))
        if not hist2[second]:
            raise ValueError('Only one grid direction found in dot lattice; supply --horizon.')
        vanishing = []
        for peak in (first, second):
            center = edges[peak] + 2.5
            lines = []
            for point, vector in vectors:
                angle = np.degrees(np.arctan2(vector[1], vector[0])) % 180
                if min(abs(angle - center), 180 - abs(angle - center)) > 6:
                    continue
                unit = vector / np.linalg.norm(vector)
                line = np.cross([point[0], point[1], 1.0], [point[0] + unit[0], point[1] + unit[1], 1.0])
                lines.append(line / np.linalg.norm(line[:2]))
            lines = np.array(lines)
            for _ in range(3):
                point = np.linalg.lstsq(lines[:, :2], -lines[:, 2], rcond=None)[0]
                errors = np.abs(lines @ np.r_[point, 1])
                keep = errors <= max(4, float(np.median(errors)) * 2.5)
                if keep.sum() < 4:
                    break
                lines = lines[keep]
            vanishing.append(np.r_[point, 1])
        return np.array(vanishing[0][:2]), np.array(vanishing[1][:2])

    @staticmethod
    def _layout(homography, warped_corners, scale):
        translation = -warped_corners.min(axis=0) * scale + 20
        matrix = np.array([[1.0, 0, translation[0]], [0, 1.0, translation[1]], [0, 0, 1.0]]) \
            @ np.array([[scale, 0, 0], [0, scale, 0], [0, 0, 1.0]]) @ homography
        size = (warped_corners.max(axis=0) - warped_corners.min(axis=0)) * scale + 40
        return matrix, tuple(int(v) for v in size)

    @staticmethod
    def _cell_period(image, matrix, size):
        from scipy.signal import find_peaks

        grid = cv2.warpPerspective(raw_grid(image), matrix, size, flags=cv2.INTER_NEAREST)
        periods = []
        for projection in (grid.sum(axis=0).astype(float), grid.sum(axis=1).astype(float)):
            projection -= projection.mean()
            corr = np.correlate(projection, projection, 'full')[len(projection) - 1:]
            peaks, _ = find_peaks(corr, distance=8)
            peaks = peaks[corr[peaks] > 0.2 * corr[0]]
            if not len(peaks):
                raise ValueError('Rectified grid period not found; calibration failed.')
            periods.append(float(peaks[0]))
        return periods

    def warp(self, image):
        return cv2.warpPerspective(image, self.matrix, self.size)

    def blue(self, image):
        """Rectified blue channel; carries the aperiodic shading that disambiguates grid aliases."""
        return self.warp(image)[:, :, 0].astype(np.float32)

    def grid_mask(self, image):
        return cv2.warpPerspective(raw_grid(image), self.matrix, self.size,
                                   flags=cv2.INTER_NEAREST).astype(np.float32) / 255

    def terrain_mask(self, image):
        return cv2.warpPerspective(terrain(image), self.matrix, self.size, flags=cv2.INTER_NEAREST)

    def dots(self, image):
        """Grid dot points via top-hat; the signal that survives in dim blank regions."""
        blue = self.warp(image)[:, :, 0]
        tophat = cv2.morphologyEx(blue, cv2.MORPH_TOPHAT,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)))
        mask = (tophat > 10).astype(np.uint8) * 255
        mask[self.valid == 0] = 0
        return mask

    def markers(self, image):
        return detect_markers(image, self.matrix)


class GridTracker:
    """Translation measurement between rectified views.

    The grid is periodic, so phase correlation alone aliases by whole cells;
    candidates are lattice aliases near the motion prediction, scored by masked
    grid correlation plus terrain IoU, then refined with ECC.
    """

    def __init__(self, grid):
        self.grid = grid
        self.center_weight = cv2.distanceTransform(grid.valid, cv2.DIST_L2, 3)

    def _candidates(self, before, after, prediction):
        cell = self.grid.cell
        radius = 3.2 * cell if prediction is None else 1.6 * cell
        anchor = (0.0, 0.0) if prediction is None else tuple(prediction)
        (px, py), _ = cv2.phaseCorrelate(before, after)
        seeds = {anchor, (0.0, 0.0)}
        for base in (anchor, (px, py)):
            for i in (-3, -2, -1, 0, 1, 2, 3):
                for j in (-3, -2, -1, 0, 1, 2, 3):
                    candidate = np.array([base[0] + i * cell, base[1] + j * cell])
                    if np.linalg.norm(candidate - anchor) <= radius:
                        seeds.add(tuple(np.round(candidate, 1)))
        return seeds

    def _score(self, before, after, before_terrain, after_terrain, shift):
        matrix = np.float32([[1, 0, -shift[0]], [0, 1, -shift[1]]])
        moved = cv2.warpAffine(after, matrix, self.grid.size, flags=cv2.INTER_LINEAR)
        common = (self.grid.valid > 0) & (cv2.warpAffine(self.grid.valid, matrix, self.grid.size,
                                                         flags=cv2.INTER_NEAREST) > 0)
        if np.count_nonzero(common) < 500:
            return None
        a, b = before[common], moved[common]
        a = a - a.mean()
        b = b - b.mean()
        norm = float(np.linalg.norm(a) * np.linalg.norm(b))
        if norm < 1:
            return None
        score = float(a @ b) / norm
        ta, tb = np.count_nonzero(before_terrain), np.count_nonzero(after_terrain)
        if min(ta, tb) >= 150:
            moved_terrain = cv2.warpAffine(after_terrain, matrix, self.grid.size, flags=cv2.INTER_NEAREST)
            x, y = (before_terrain > 0) & common, (moved_terrain > 0) & common
            union = int((x | y).sum())
            if union:
                score += 1.5 * float((x & y).sum() / union)
        return score

    def measure(self, before, after, before_terrain, after_terrain, prediction):
        """Translation aligning `after` onto `before`; the camera moved by -delta.

        Along featureless straight roads every lattice alias scores equally
        (aperture problem), so the candidate nearest to the motion-model
        prediction wins; scores are only a sanity floor. A zero shift outscores
        the predicted one only when the camera actually clamped at an edge.
        """
        prediction = None if prediction is None else np.asarray(prediction, float)
        scored = []
        for seed in self._candidates(before, after, prediction):
            score = self._score(before, after, before_terrain, after_terrain, seed)
            if score is not None:
                scored.append((np.asarray(seed, float), score))
        if not scored:
            return None
        zero = next((item for item in scored if not item[0].any()), None)
        plausible = [item for item in scored if item[1] >= 0.5]
        if not plausible:
            return None
        if prediction is None:
            best = max(plausible, key=lambda item: item[1])[0]
        else:
            best = min(plausible, key=lambda item: np.linalg.norm(item[0] - prediction))[0]
        best_score = self._score(before, after, before_terrain, after_terrain, best)
        if (prediction is not None and zero is not None and np.linalg.norm(prediction) > 1.5 * self.grid.cell
                and zero[1] >= best_score - 0.05):
            best = np.zeros(2)  # Camera clamped at an edge: the identical view wins outright.
        matrix = np.float32([[1, 0, best[0]], [0, 1, best[1]]])
        try:
            _, refined = cv2.findTransformECC(
                before, after, matrix, cv2.MOTION_TRANSLATION,
                (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 60, 1e-6), self.grid.valid, 5)
            delta = refined[:, 2].astype(float)
            if np.linalg.norm(delta - best) < self.grid.cell / 2:
                best = delta
        except cv2.error:
            pass
        score = self._score(before, after, before_terrain, after_terrain, best)
        if score is None:
            return None
        return {'delta': best.tolist(), 'score': round(float(score), 4)}

    def measure_terrain(self, before_terrain, after_terrain, prediction):
        """Alias-free registration on terrain alone; needs >=150 terrain px in both views.

        ECC converges only within about one cell, so candidates are lattice
        aliases scored by raw terrain IoU first; the winner is ECC-refined.
        """
        if min(np.count_nonzero(before_terrain), np.count_nonzero(after_terrain)) < 150:
            return None
        stationary_iou, stationary_area = overlap(before_terrain, after_terrain, self.grid.valid, (0, 0))
        if stationary_iou >= 0.995 and stationary_area >= 80:
            return {'delta': [0.0, 0.0], 'score': 2.0}
        a = cv2.GaussianBlur(before_terrain.astype(np.float32) / 255, (7, 7), 0)
        b = cv2.GaussianBlur(after_terrain.astype(np.float32) / 255, (7, 7), 0)
        phase, _ = cv2.phaseCorrelate(a, b)
        anchor = (0.0, 0.0) if prediction is None else tuple(np.asarray(prediction, float))
        cell = self.grid.cell
        seeds = set()
        for base in {anchor, tuple(np.round(phase, 1)), (0.0, 0.0)}:
            for i in (-3, -2, -1, 0, 1, 2, 3):
                for j in (-3, -2, -1, 0, 1, 2, 3):
                    candidate = np.array([base[0] + i * cell, base[1] + j * cell])
                    if np.linalg.norm(candidate - anchor) <= 3.2 * cell:
                        seeds.add(tuple(candidate))
        scored = []
        for seed in seeds:
            iou, area = overlap(before_terrain, after_terrain, self.grid.valid, seed)
            if area >= 80:
                scored.append((np.asarray(seed), iou))
        if not scored:
            return None
        refined = []
        for seed, _ in sorted(scored, key=lambda item: -item[1])[:4]:
            matrix = np.float32([[1, 0, seed[0]], [0, 1, seed[1]]])
            try:
                _, matrix = cv2.findTransformECC(
                    a, b, matrix, cv2.MOTION_TRANSLATION,
                    (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 150, 1e-6), self.grid.valid, 5)
                delta = matrix[:, 2].astype(float)
                if np.linalg.norm(delta - seed) >= cell:
                    continue
            except cv2.error:
                continue
            iou, area = overlap(before_terrain, after_terrain, self.grid.valid, delta)
            if area >= 80:
                refined.append((delta, iou))
        if refined:
            top = max(iou for _, iou in refined)
            tied = [delta for delta, iou in refined if iou >= top - 0.02]
            # Aperture ties along straight roads are broken by the motion prediction.
            if prediction is not None and len(tied) > 1:
                delta = min(tied, key=lambda d: np.linalg.norm(d - np.asarray(prediction, float)))
                iou = next(i for d, i in refined if d is delta)
            else:
                delta, iou = max(refined, key=lambda item: item[1])
        else:
            delta, iou = max(scored, key=lambda item: item[1])
        if iou < 0.85 or np.linalg.norm(delta) > max(before_terrain.shape) * 0.65:
            return None
        return {'delta': delta.tolist(), 'score': round(float(1 + iou), 4)}

    def measure_dots(self, before_dots, after_dots, prediction):
        """Last-resort tracking through blank regions using grid dots.

        Dots are exactly periodic, so the alias nearest the velocity prediction
        wins; the pose is re-anchored against terrain when roads reappear.
        """
        if min(np.count_nonzero(before_dots), np.count_nonzero(after_dots)) < 3000:
            return None
        cell = self.grid.cell
        kernel = np.ones((5, 5), np.uint8)
        dilated = cv2.dilate(before_dots, kernel)
        total = np.count_nonzero(before_dots) + np.count_nonzero(after_dots)
        anchor = np.zeros(2) if prediction is None else np.asarray(prediction, float)
        scored = []
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                seed = anchor + np.array([i * cell, j * cell])
                matrix = np.float32([[1, 0, -seed[0]], [0, 1, -seed[1]]])
                moved = cv2.warpAffine(after_dots, matrix, self.grid.size, flags=cv2.INTER_NEAREST)
                dice = 2.0 * np.count_nonzero((dilated > 0) & (moved > 0)) / total
                scored.append((seed, float(dice)))
        top = max(score for _, score in scored)
        if top < 0.3:
            return None
        tied = [seed for seed, score in scored if score >= top - 0.05]
        best = min(tied, key=lambda seed: np.linalg.norm(seed - anchor))
        a = cv2.GaussianBlur(before_dots.astype(np.float32) / 255, (5, 5), 0)
        b = cv2.GaussianBlur(after_dots.astype(np.float32) / 255, (5, 5), 0)
        matrix = np.float32([[1, 0, best[0]], [0, 1, best[1]]])
        try:
            _, refined = cv2.findTransformECC(
                a, b, matrix, cv2.MOTION_TRANSLATION,
                (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 100, 1e-6), self.grid.valid, 5)
            delta = refined[:, 2].astype(float)
            if np.linalg.norm(delta - best) < cell / 2:
                best = delta
        except cv2.error:
            pass
        return {'delta': best.tolist(), 'score': round(float(top), 4)}


class RasterScanner:
    """Boundary-aware serpentine scan; the camera pose is always measured, never assumed."""

    def __init__(self, args, window):
        self.args, self.window = args, window
        self.output = args.output
        self.output.mkdir(parents=True, exist_ok=True)
        if (self.output / 'scan.json').exists():
            raise ValueError('Output already contains a scan; use a new directory or --rebuild.')
        first = window.capture()
        try:
            self.grid = MetricGrid(first, cell_px=args.cell_px)
        except ValueError:
            cv2.imwrite(str(self.output / 'calibration_failed.png'), first)
            raise
        self.tracker = GridTracker(self.grid)
        self.data = {
            'version': 2, 'roi': args.roi, 'client': args.client, 'cell_px': self.grid.cell,
            'matrix': self.grid.matrix.tolist(), 'warp_size': list(self.grid.size),
            'frames': [], 'moves': [], 'clamps': [], 'status': 'running',
            'coverage_definition': 'BFS over road frontiers visible at the view edge; sides without terrain are never entered',
        }
        self.position = np.zeros(2)
        self.current = None
        self.current_terrain = None
        self.current_raw = None
        self.jacobian = None

    def record(self, image, kind, save=True):
        if len(self.data['frames']) >= self.args.max_frames:
            raise RuntimeError('Frame budget reached; scan is incomplete, not a map boundary.')
        index = len(self.data['frames'])
        name = f'frame_{index:05d}.png'
        if not cv2.imwrite(str(self.output / name), image):
            raise OSError(f'Could not save {name}')
        rectified = self.grid.warp(image)
        terrain_mask = self.grid.terrain_mask(image)
        self_mask, enemies = self.grid.markers(image)
        self.current_raw = image
        self.current = rectified[:, :, 0].astype(np.float32)
        self.current_terrain = terrain_mask
        frame = {
            'id': index, 'file': name, 'kind': kind, 'position': self.position.tolist(),
            'terrain_pixels': int(np.count_nonzero(terrain_mask)),
            'self': self_mask[0] if self_mask else None, 'enemies': enemies,
        }
        self.data['frames'].append(frame)
        if save:
            self.save()
        print(json.dumps({'id': index, 'kind': kind, 'position': [round(v, 1) for v in self.position],
                          'terrain_pixels': frame['terrain_pixels']}, ensure_ascii=False), flush=True)
        return frame

    def save(self):
        temporary = self.output / 'scan.json.tmp'
        temporary.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        # Defender/indexer can briefly lock the target; os.replace fails with WinError 5 then.
        for _ in range(10):
            try:
                temporary.replace(self.output / 'scan.json')
                return
            except PermissionError:
                time.sleep(0.3)
        temporary.replace(self.output / 'scan.json')

    def swipe(self, screen, prediction):
        if stop_requested(self.args, self.output):
            raise RuntimeError('Stopped by STOP file; scan remains incomplete.')
        before, before_terrain = self.current, self.current_terrain
        before_id = len(self.data['frames']) - 1
        self.window.drag_vector(float(screen[0]), float(screen[1]))
        image = self.window.capture()
        after_terrain = self.grid.terrain_mask(image)
        # Terrain is aperiodic and alias-free, so it is the primary measurement;
        # the grid/blue tracker only covers fringe views without enough terrain.
        match = self.tracker.measure_terrain(before_terrain, after_terrain,
                                             (0.0, 0.0) if prediction is None else prediction)
        if match is None:
            blue = self.grid.warp(image)[:, :, 0].astype(np.float32)
            match = self.tracker.measure(before, blue, before_terrain, after_terrain, prediction)
        if match is None:
            self.record(image, 'untracked')
            raise RuntimeError('Tracking lost; scan stopped without inventing a camera position.')
        self.position -= np.asarray(match['delta'])
        self.record(image, 'move')
        self.data['moves'].append({'a': before_id, 'b': len(self.data['frames']) - 1,
                                   'delta': match['delta'], 'corr': match['score']})
        self.save()
        return match

    def hunt_terrain(self):
        """Drag any visible terrain strip back into the view centre before calibration.

        Hunt frames are not recorded: the camera pose is only tracked from the
        first calibrated frame on.
        """
        for _ in range(4):
            image = self.window.capture()
            mask = terrain(image)
            if np.count_nonzero(mask) >= 800:
                return
            if mask.any():
                ys, xs = np.nonzero(mask)
                sx, sy = float(xs.mean()), float(ys.mean())
            else:
                sx, sy = 0.0, 0.0  # Nothing visible; guess leftward.
            height, width = mask.shape
            vector = np.array([width / 2 - sx, height / 2 - sy])
            length = float(np.linalg.norm(vector))
            if length < 1:
                vector = np.array([0.0, -1.0])
            else:
                vector /= length
            self.window.drag_vector(*(vector * 150))
        if np.count_nonzero(terrain(self.window.capture())) < 800:
            raise RuntimeError('No terrain found within four hunt drags; open the map over a road.')

    def calibrate_motion(self):
        """Alias-free screen->world Jacobian from terrain registration over two drag axes.

        Drags pull content toward the terrain centroid, so the camera moves
        into the map instead of off the roads; the return drag doubles as a
        consistency check within half a cell.
        """
        step = float(self.args.step)

        def directed(vector):
            # Content follows the drag; pull the terrain centroid toward the view centre.
            mask = terrain(self.current_raw)
            if not mask.any():
                return vector
            moments = cv2.moments(mask)
            centroid = (moments['m10'] / moments['m00'], moments['m01'] / moments['m00'])
            center = (mask.shape[1] / 2, mask.shape[0] / 2)
            axis = 0 if vector[0] else 1
            return vector if centroid[axis] < center[axis] else -vector

        vy = directed(np.array((0.0, step)))
        forward = self.swipe(vy, prediction=(0.0, 0.0))
        vx = directed(np.array((step, 0.0)))
        side = self.swipe(vx, prediction=(0.0, 0.0))
        delta_f = np.asarray(forward['delta'])
        back = self.swipe(-vy, prediction=-delta_f)
        delta_b = np.asarray(back['delta'])
        if np.linalg.norm(delta_f + delta_b) > self.grid.cell / 2:
            raise RuntimeError(f'Motion model inconsistent: residual {np.round(delta_f + delta_b, 1).tolist()} px.')
        column_y = (delta_b - delta_f) / (2 * vy[1])
        column_x = -np.asarray(side['delta']) / vx[0]
        self.jacobian = np.array([[column_x[0], column_y[0]], [column_x[1], column_y[1]]])
        if abs(np.linalg.det(self.jacobian)) < 0.005:
            raise RuntimeError(f'Motion calibration degenerate: {self.jacobian.tolist()}')
        self.data['jacobian'] = self.jacobian.tolist()
        self.save()

    def move_towards(self, target, attempts=6):
        target = np.asarray(target, float)
        for _ in range(attempts):
            delta = target - self.position
            if np.linalg.norm(delta) < 25:
                return True
            screen = np.linalg.solve(self.jacobian, delta)
            length = float(np.linalg.norm(screen))
            if length < self.args.min_swipe:
                return True  # Below the game's drag dead zone; already close enough.
            if length > self.args.max_swipe:
                screen *= self.args.max_swipe / length
            self.swipe(screen, prediction=-(self.jacobian @ screen))
        return False

    def frontier_sides(self):
        """World-axis directions whose view edge still shows terrain: unexplored road that way."""
        valid = self.grid.valid > 0
        terr = self.current_terrain > 0
        margin = int(round(self.grid.cell * 0.6))
        sides = []
        for axis, sign in ((0, -1), (0, 1), (1, -1), (1, 1)):
            ahead = np.zeros_like(valid)
            if axis == 0 and sign == 1:
                ahead[:, :-margin] = valid[:, margin:]
            elif axis == 0:
                ahead[:, margin:] = valid[:, :-margin]
            elif sign == 1:
                ahead[:-margin, :] = valid[margin:, :]
            else:
                ahead[margin:, :] = valid[:-margin, :]
            sides.append(np.count_nonzero(terr & (valid & ~ahead)) >= 40)
        return sides

    def explore_frontier(self):
        """BFS over road frontiers; sides whose view edge has no terrain are never entered."""
        from collections import deque

        self.hunt_terrain()
        self.record(self.window.capture(), 'origin')
        self.calibrate_motion()
        queue = deque([self.position.copy()])
        visited = set()
        resolution = self.args.grid_step / 2
        while queue:
            node = queue.popleft()
            key = tuple(np.round(node / resolution).astype(int))
            if key in visited:
                continue
            visited.add(key)
            if stop_requested(self.args, self.output):
                raise RuntimeError('Stopped by STOP file; scan remains incomplete.')
            before_id = len(self.data['frames']) - 1
            self.move_towards(node)
            if len(self.data['frames']) - 1 == before_id:
                self.record(self.window.capture(), 'node')
            sides = self.frontier_sides()
            self.data['frames'][-1]['frontier'] = sides
            self.save()
            for (axis, sign), has in zip(((0, -1), (0, 1), (1, -1), (1, 1)), sides):
                if not has:
                    continue
                nxt = self.position.copy()
                nxt[axis] += sign * self.args.grid_step
                if tuple(np.round(nxt / resolution).astype(int)) in visited:
                    continue
                if any(np.linalg.norm(nxt - queued) <= resolution for queued in queue):
                    continue
                queue.append(nxt)
        self.data['status'] = 'roads_exhausted'
        self.save()


class FlowTracker:
    """Track small raw-image motions before transforming points onto the grid plane."""

    def __init__(self, grid):
        self.grid = grid

    @staticmethod
    def background(image):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (85, 40, 25), (120, 255, 220))
        # Roads and click flashes must not displace the persistent ground grid.
        mask[cv2.dilate(terrain(image), np.ones((17, 17), np.uint8)) > 0] = 0
        mask[:10] = mask[-10:] = 0
        mask[:, :10] = mask[:, -10:] = 0
        mask[-45:, -85:] = 0
        return mask

    def measure(self, before, after):
        a = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
        b = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)
        points = cv2.goodFeaturesToTrack(a, 350, 0.01, 8, mask=self.background(before))
        if points is None or len(points) < 24:
            return None
        options = dict(winSize=(21, 21), maxLevel=3,
                       criteria=(cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 20, 0.01))
        following, ok, error = cv2.calcOpticalFlowPyrLK(a, b, points, None, **options)
        back, back_ok, _ = cv2.calcOpticalFlowPyrLK(b, a, following, None, **options)
        keep = (ok.ravel() > 0) & (back_ok.ravel() > 0) & (error.ravel() < 25)
        keep &= np.linalg.norm((points - back).reshape(-1, 2), axis=1) < 0.8
        xy = np.rint(following.reshape(-1, 2)).astype(int)
        xy[:, 0] = np.clip(xy[:, 0], 0, b.shape[1] - 1)
        xy[:, 1] = np.clip(xy[:, 1], 0, b.shape[0] - 1)
        keep &= self.background(after)[xy[:, 1], xy[:, 0]] > 0
        if keep.sum() < 24:
            return None
        shifts = (cv2.perspectiveTransform(following[keep], self.grid.matrix)
                  - cv2.perspectiveTransform(points[keep], self.grid.matrix)).reshape(-1, 2)
        delta = np.median(shifts, axis=0)
        residuals = np.linalg.norm(shifts - delta, axis=1)
        inliers = residuals < max(2.0, 2.5 * float(np.median(residuals)))
        if inliers.sum() < 24 or inliers.mean() < 0.55:
            return None
        delta = np.median(shifts[inliers], axis=0)
        spread = float(np.median(np.linalg.norm(shifts[inliers] - delta, axis=1)))
        if spread > 4:
            return None
        return {'delta': delta.astype(float).tolist(), 'score': float(inliers.mean()),
                'features': int(inliers.sum()), 'spread': spread}


def stationary_tail(samples, minimum_travel=60.0):
    """Require tracked stationary views while the physical cursor keeps moving."""
    travel = 0.0
    count = 0
    for sample in reversed(samples):
        cursor = float(np.linalg.norm(sample['cursor_delta']))
        if np.linalg.norm(sample['delta']) > 1.2 or sample['spread'] > 1.0:
            break
        if cursor < 0.5:
            continue
        travel += cursor
        count += 1
    return count >= 3 and travel >= minimum_travel


def stationary_stroke(samples):
    """Subpixel grid shimmer can accumulate; require a whole driven gesture near zero."""
    if len(samples) < 8:
        return False
    cursor_travel = sum(float(np.linalg.norm(sample['cursor_delta'])) for sample in samples)
    motion = np.array([np.linalg.norm(sample['delta']) for sample in samples])
    spread = np.array([sample['spread'] for sample in samples])
    return bool(cursor_travel >= 120 and motion.max() < 2
                and np.percentile(motion, 90) < 1.2 and np.percentile(spread, 90) < 0.7)


class DriftScanner(RasterScanner):
    """Stream complete driver gestures while tracking the map on the capture thread."""

    def __init__(self, args, window):
        super().__init__(args, window)
        self.flow = FlowTracker(self.grid)
        self.cursor_map = None
        self.last_stroke = None
        self.frontiers = []
        self.visited = []
        self.camera_limits = {}
        self.data['strokes'] = []
        self.data['capture_mode'] = 'live_keyframes'
        self.data['display_orientation'] = getattr(args, 'orientation', 'screen_oblique')
        self.data['terrain_edges'] = []
        self.data['unresolved_frontiers'] = []
        self.data['coverage_definition'] = 'visible blue-road frontiers; empty grid is not explored as new map'

    def store_pose(self, image, kind):
        previous = self.data['frames'][-1]
        frame = self.record(image, kind, save=False)
        self.data['moves'].append({'a': previous['id'], 'b': frame['id'],
                                   'delta': (np.asarray(previous['position']) - self.position).tolist(),
                                   'corr': 1.0})
        self.queue_frontiers()

    def queue_frontiers(self):
        radius = self.args.grid_step * 0.55
        for direction, visible in zip(((-1, 0), (1, 0), (0, -1), (0, 1)), self.frontier_sides()):
            if not visible:
                continue
            target = self.clip_target(self.position + np.asarray(direction) * self.args.grid_step)
            if any(np.linalg.norm(target - other) < radius for other in self.frontiers + self.visited):
                continue
            self.frontiers.append(target)

    def clip_target(self, target):
        target = target.copy()
        for (axis, sign), value in self.camera_limits.items():
            if sign * (target[axis] - value) > 0:
                target[axis] = value
        return target

    def stroke(self, cursor_vec):
        from module.device.win.virtual_mouse.input import BTN_LEFT

        if stop_requested(self.args, self.output):
            raise RuntimeError('Stopped by STOP file; scan remains incomplete.')
        self.window.focus()
        handler = self.window.handler
        ox, oy = self.window.gui.ClientToScreen(self.window.hwnd, (0, 0))
        left, top, right, bottom = self.args.roi
        lo = np.array([left + 35, top + 35], float)
        hi = np.array([right - 35, bottom - 35], float)
        vec = np.asarray(cursor_vec, float)
        vec /= max(1.0, float(np.max(np.abs(vec) / (hi - lo))))
        start = (lo + hi - vec) / 2
        duration = max(0.25, float(np.linalg.norm(vec)) / self.args.drag_speed)
        cancel = Event()
        pressed = Event()
        interval = min(self.args.capture_interval, self.args.capture_px / self.args.drag_speed * 0.6)
        captures = []
        measurements = []
        before_position = self.position.copy()
        before_id = self.data['frames'][-1]['id']
        began = time.perf_counter()

        def sample():
            image = self.window.capture()
            cursor = np.asarray(handler.mouse_driver.cursor(), float)
            stamp = time.perf_counter()
            previous = captures[-1][0] if captures else self.current_raw
            match = self.flow.measure(previous, image)
            if match is None and not captures:
                match = self.tracker.measure_terrain(
                    self.grid.terrain_mask(previous), self.grid.terrain_mask(image), None)
                if match is not None:
                    match.update(features=0, spread=0.0)
            cursor_delta = cursor - captures[-1][1] if len(captures) > 1 else np.zeros(2)
            large_gap = np.linalg.norm(cursor_delta) > self.args.capture_px * 1.8
            uncertain_gap = match is not None and (match['spread'] > 2
                                                    or np.linalg.norm(match['delta']) > self.grid.cell / 2)
            if match is None or (large_gap and uncertain_gap):
                cv2.imwrite(str(self.output / 'tracking_before.png'), previous)
                cv2.imwrite(str(self.output / 'tracking_failed.png'), image)
                (self.output / 'tracking_failure.json').write_text(json.dumps({
                    'sample': len(captures), 'match': match, 'cursor_delta': cursor_delta.tolist(),
                    'capture_interval': stamp - captures[-1][2] if captures else None,
                }), encoding='utf-8')
                raise RuntimeError('Capture gap or grid tracking failure; no map boundary inferred.')
            self.position -= np.asarray(match['delta'])
            match['cursor_delta'] = cursor_delta.tolist()
            match['timestamp'] = stamp - began
            if captures:
                measurements.append(match)
            captures.append((image, cursor, stamp))
            last_position = np.asarray(self.data['frames'][-1]['position'])
            if np.linalg.norm(self.position - last_position) >= self.args.keyframe_px:
                self.store_pose(image, 'drift')
            return match

        def drag():
            with handler._lock:
                try:
                    handler.mouse_down(round(ox + start[0]), round(oy + start[1]))
                    if handler._failures:
                        raise RuntimeError('Driver reported a failed press.')
                    pressed.set()
                    if cancel.wait(0.06):
                        return
                    point = start + vec
                    steps = max(8, round(duration / 0.004))
                    ok = handler.mouse_driver.drag_stream(
                        round(ox + point[0]), round(oy + point[1]), steps, duration / steps,
                        buttons=BTN_LEFT)
                    if not ok:
                        raise RuntimeError('Driver reported a failed drag.')
                finally:
                    handler.mouse_up()
                    pressed.set()

        sample()
        with ThreadPoolExecutor(max_workers=1) as executor:
            job = executor.submit(drag)
            try:
                if not pressed.wait(2):
                    raise RuntimeError('Driver did not begin the drag.')
                while not job.done():
                    tick = time.perf_counter()
                    if stop_requested(self.args, self.output):
                        raise RuntimeError('Stopped by STOP file; scan remains incomplete.')
                    sample()
                    cancel.wait(max(0, interval - (time.perf_counter() - tick)))
                job.result()
                stable = 0
                settling = self.cursor_map is None
                for _ in range(45 if settling else 3):
                    tail = sample()
                    stable = stable + 1 if np.linalg.norm(tail['delta']) < 0.4 else 0
                    if stable >= 3:
                        break
                    time.sleep(interval)
                else:
                    if settling:
                        raise RuntimeError('Camera did not settle after release; capture stopped.')
            finally:
                cancel.set()
        input_seconds = time.perf_counter() - began
        still = stationary_stroke(measurements)
        if still and np.linalg.norm(self.position - before_position) < self.grid.cell / 3:
            self.position = before_position
        else:
            still = False
        self.store_pose(captures[-1][0], 'node')
        result = {
            'frames': [before_id, self.data['frames'][-1]['id']],
            'cursor_vector': vec.tolist(),
            'camera_delta': (self.position - before_position).tolist(),
            'stationary_tail': still or stationary_tail(measurements),
            'stationary_stroke': still,
            'samples': len(measurements),
            'input_seconds': round(input_seconds, 3),
            'total_seconds': round(time.perf_counter() - began, 3),
            'max_capture_gap': round(max(b[2] - a[2] for a, b in zip(captures, captures[1:])), 3),
        }
        self.last_stroke = result
        self.data['strokes'].append(result)
        self.save()
        print(json.dumps({'stroke': result}), flush=True)
        return measurements

    def calibrate_cursor(self):
        columns = []
        for axis in (0, 1):
            direction = np.eye(2)[axis]
            samples = self.stroke(direction * 240)
            steady = [(-np.asarray(sample['delta']) / sample['cursor_delta'][axis])
                      for sample in samples[3:] if abs(sample['cursor_delta'][axis]) >= 3
                      and np.linalg.norm(sample['delta']) >= 3]
            if len(steady) < 5:
                raise RuntimeError('Insufficient moving calibration samples; start away from a camera edge.')
            columns.append(np.asarray(self.last_stroke['camera_delta'])
                           / self.last_stroke['cursor_vector'][axis])
        self.cursor_map = np.asarray(columns).T
        if abs(np.linalg.det(self.cursor_map)) < 0.05 or np.linalg.cond(self.cursor_map) > 4:
            raise RuntimeError(f'Cursor calibration degenerate: {self.cursor_map.tolist()}')
        self.data['cursor_map'] = self.cursor_map.tolist()
        self.save()

    def explore_drift(self):
        self.record(self.window.capture(), 'origin')
        if np.count_nonzero(self.current_terrain) < 800:
            raise RuntimeError('Open the minimap over a blue road before scanning.')
        self.visited.append(self.position.copy())
        self.queue_frontiers()
        self.calibrate_cursor()
        radius = self.args.grid_step * 0.55
        while self.frontiers:
            index = min(range(len(self.frontiers)), key=lambda i: np.linalg.norm(self.frontiers[i] - self.position))
            target = self.clip_target(self.frontiers.pop(index))
            if any(np.linalg.norm(target - other) < radius for other in self.visited):
                continue
            self.visited.append(target.copy())
            reached = False
            first_frame = self.data['frames'][-1]['id']
            stalls = {}
            travel = np.linalg.norm(np.linalg.solve(self.cursor_map, target - self.position))
            attempts = min(24, 4 + int(np.ceil(travel / self.args.stroke_px)))
            for _ in range(attempts):
                delta = target - self.position
                if np.linalg.norm(delta) < self.args.grid_step * 0.4:
                    reached = True
                    break
                cursor = np.linalg.solve(self.cursor_map, delta)
                length = float(np.linalg.norm(cursor))
                cursor *= min(self.args.stroke_px, max(120.0, length)) / length
                self.stroke(cursor)
                axis = int(np.abs(delta).argmax())
                sign = int(np.sign(delta[axis]))
                step = self.last_stroke['camera_delta'][axis]
                key = (axis, sign)
                stalls[key] = stalls.get(key, 0) + 1 if abs(step) < 5 else 0
                if stalls[key] >= 2:
                    self.camera_limits[key] = float(self.position[axis])
                    self.data['clamps'].append({
                        'axis': axis, 'sign': sign, 'value': float(self.position[axis]),
                        'frames': [first_frame, self.data['frames'][-1]['id']],
                        'evidence': 'two drags with no progress on the requested axis',
                    })
                    target = self.clip_target(target)
                if np.count_nonzero(self.current_terrain) < 150:
                    self.data['terrain_edges'].append({
                        'target': target.tolist(), 'position': self.position.tolist(),
                        'frames': [first_frame, self.data['frames'][-1]['id']],
                        'reason': 'no blue road remains in this view',
                    })
                    print(json.dumps({'terrain_edge': self.data['terrain_edges'][-1]}), flush=True)
                    reached = True
                    break
                if np.linalg.norm(target - self.position) < self.args.grid_step * 0.4:
                    reached = True
                    break
            if not reached:
                delta = target - self.position
                at_limit = any(abs(float(target[axis]) - value) < 1.0
                               for (axis, _), value in self.camera_limits.items())
                if at_limit:
                    # The camera domain ends here; panning cannot show anything beyond it.
                    self.data['terrain_edges'].append({
                        'target': target.tolist(), 'position': self.position.tolist(),
                        'frames': [first_frame, self.data['frames'][-1]['id']],
                        'reason': 'frontier at the camera pan limit; beyond is unreachable',
                    })
                elif np.linalg.norm(delta) < self.args.grid_step * 2:
                    # Corner sliding prevents centering, but the target is inside
                    # the observed view, so its content is captured regardless.
                    self.data['terrain_edges'].append({
                        'target': target.tolist(), 'position': self.position.tolist(),
                        'frames': [first_frame, self.data['frames'][-1]['id']],
                        'reason': 'observed from a nearby view; corner sliding prevented centering',
                    })
                else:
                    self.data['unresolved_frontiers'].append(target.tolist())
            self.visited.append(self.position.copy())
            self.save()
        if self.data['unresolved_frontiers']:
            raise RuntimeError('Some road frontiers could not be reached; map coverage remains incomplete.')
        self.data['status'] = 'roads_exhausted'
        self.save()


def merge_enemy_markers(observations, radius):
    groups = []
    for observation in observations:
        point = np.asarray(observation['position'])
        candidates = []
        for index, group in enumerate(groups):
            if any(item['frame'] == observation['frame'] for item in group):
                continue
            center = np.median([item['position'] for item in group], axis=0)
            distance = np.linalg.norm(point - center)
            if distance <= radius:
                candidates.append((distance, index))
        if candidates:
            groups[min(candidates)[1]].append(observation)
        else:
            groups.append([observation])
    result = [{'position': np.median([item['position'] for item in group], axis=0).round(1).tolist(),
               'frames': [item['frame'] for item in group], 'observations': len(group)} for group in groups]
    result.sort(key=lambda item: (item['position'][1], item['position'][0]))
    for index, item in enumerate(result, 1):
        item['id'] = f'E{index:02d}'
    return result


def marker_contributes(observation, source_frames, radius):
    x, y = np.rint(observation['position']).astype(int)
    height, width = source_frames.shape
    if not 0 <= x < width or not 0 <= y < height:
        return False
    if source_frames[y, x] == observation['frame']:
        return True
    radius = max(1, int(round(radius)))
    patch = source_frames[max(0, y - radius):y + radius + 1, max(0, x - radius):x + radius + 1]
    # A seam can split a hollow symbol while both views' centers fall in the other view.
    return (patch == observation['frame']).mean() >= 0.2


def annotate_markers(image, player, enemies):
    result = image.copy()
    marks = [(item['id'], item['position'], (125, 115, 255)) for item in enemies]
    if player is not None:
        marks.append(('ME', player['position'], (90, 255, 100)))
    for label, point, color in marks:
        x, y = (int(round(value)) for value in point)
        cv2.circle(result, (x, y), 22, (12, 18, 24), 6, cv2.LINE_AA)
        cv2.circle(result, (x, y), 22, color, 3, cv2.LINE_AA)
        cv2.drawMarker(result, (x, y), color, cv2.MARKER_CROSS, 14, 2, cv2.LINE_AA)
        text_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0][0]
        location = (max(3, min(x + 27, result.shape[1] - text_width - 3)), max(22, y - 22))
        cv2.putText(result, label, location, cv2.FONT_HERSHEY_SIMPLEX, 0.8, (12, 18, 24), 6, cv2.LINE_AA)
        cv2.putText(result, label, location, cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
    legend = f'ME: player | E: enemy ({len(enemies)})'
    cv2.rectangle(result, (8, 8), (515, 49), (12, 18, 24), -1)
    cv2.putText(result, legend, (19, 37), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (240, 240, 240), 2, cv2.LINE_AA)
    return result


class JointRegistration:
    """Align roads using the separately measured motion of the perspective grid."""

    def __init__(self, matrix, warp_size, source, cell):
        self.scale = min(0.25, 320 / max(warp_size))
        self.matrix = np.diag([self.scale, self.scale, 1]) @ matrix
        self.size = tuple(max(1, round(v * self.scale)) for v in warp_size)
        self.valid = (cv2.warpPerspective(source, self.matrix, self.size,
                                          flags=cv2.INTER_NEAREST) > 0).astype(np.float32)
        self.cell = cell
        self.center = np.array(self.valid.shape[::-1]) - 1
        yy, xx = np.indices(tuple(2 * n - 1 for n in self.valid.shape))
        self.shifts = np.stack([xx - self.center[0], yy - self.center[1]], axis=-1) / self.scale

    def features(self, image):
        roads = cv2.warpPerspective(terrain(image), self.matrix, self.size).astype(np.float32) / 255
        roads *= self.valid
        grid = cv2.warpPerspective(raw_grid(image), self.matrix, self.size).astype(np.float32) / 255
        background = self.valid * (cv2.dilate(roads, np.ones((5, 5), np.uint8)) < 0.1)
        grid = cv2.GaussianBlur(grid, (5, 5), 0.7) * background
        return tuple(np.uint8(np.clip(value * 255, 0, 255)) for value in (roads, grid, background))

    def correlate(self, first, second):
        from scipy.signal import correlate

        a, ga, va = (value.astype(np.float32) / 255 for value in first)
        b, gb, vb = (value.astype(np.float32) / 255 for value in second)

        def corr(x, y):
            return correlate(x, y, mode='full', method='fft')

        intersection = corr(b, a)
        union = corr(self.valid, a) + corr(b, self.valid) - intersection
        iou = np.clip(intersection / np.maximum(union, 1), 0, 1)
        norm = corr(vb, ga * ga) + corr(gb * gb, va)
        grid = np.clip(2 * corr(gb, ga) / np.maximum(norm, 0.1), 0, 1)
        return iou, grid, union, corr(vb, va)

    def peak(self, scores, allowed):
        if not np.any(allowed):
            return None
        candidates = np.where(allowed, scores, -10)
        y, x = np.unravel_index(candidates.argmax(), candidates.shape)
        delta = self.shifts[y, x].copy()
        if 0 < x < scores.shape[1] - 1 and 0 < y < scores.shape[0] - 1:
            for axis, values in enumerate(((scores[y, x - 1], scores[y, x], scores[y, x + 1]),
                                            (scores[y - 1, x], scores[y, x], scores[y + 1, x]))):
                left, middle, right = values
                curvature = left - 2 * middle + right
                if curvature < -1e-5:
                    delta[axis] += np.clip(0.5 * (left - right) / curvature, -0.5, 0.5) / self.scale
        competing = allowed & (np.linalg.norm(self.shifts - self.shifts[y, x], axis=2) >= self.cell * 0.55)
        gap = float(scores[y, x] - np.max(scores[competing])) if np.any(competing) else 0.0
        return delta, (y, x), gap

    @staticmethod
    def fit_motion(grid_moves, road_moves, cell):
        from scipy.optimize import least_squares

        grid, roads = np.asarray(grid_moves), np.asarray(road_moves)
        if len(grid) < 12:
            return None
        singular = np.linalg.svd(grid, compute_uv=False)
        if singular[-1] < singular[0] * 0.15:
            return None
        fit = least_squares(lambda x: (grid @ x.reshape(2, 2) - roads).ravel(), np.eye(2).ravel(),
                            loss='soft_l1', f_scale=cell / 24)
        matrix = fit.x.reshape(2, 2)
        error = np.linalg.norm(grid @ matrix - roads, axis=1)
        inliers = error < cell * 0.25
        scales = np.linalg.svd(matrix, compute_uv=False)
        if inliers.sum() < 12 or inliers.mean() < 0.6 or scales.min() < 0.6 or scales.max() > 2:
            return None
        return matrix, {'samples': len(grid), 'inliers': int(inliers.sum()),
                        'median_error_px': float(np.median(error[inliers]))}

    def measure_roads(self, first, second, prediction, radius):
        roads, _, area, _ = self.correlate(first, second)
        allowed = (np.linalg.norm(self.shifts - prediction, axis=2) < radius)
        allowed &= area > 1600 * self.scale ** 2
        peak = self.peak(roads, allowed)
        if peak is None:
            return None
        delta, point, gap = peak
        # Distinct road shapes constrain position even when the background grid has different parallax.
        if roads[point] < 0.9 or gap < 0.06:
            return None
        return {'delta': delta.tolist(), 'road_iou': float(roads[point]), 'gap': gap,
                'prediction_error_px': float(np.linalg.norm(delta - prediction))}

    def measure(self, first, second, prediction, motion_map, radius):
        roads, grid, road_area, grid_area = self.correlate(first, second)
        # Grid and roads move together, but their rendered layers need not have equal displacement.
        grid_shifts = self.shifts @ np.linalg.inv(motion_map)
        map_x = (grid_shifts[:, :, 0] * self.scale + self.center[0]).astype(np.float32)
        map_y = (grid_shifts[:, :, 1] * self.scale + self.center[1]).astype(np.float32)
        grid = cv2.remap(grid, map_x, map_y, cv2.INTER_LINEAR)
        grid_area = cv2.remap(grid_area, map_x, map_y, cv2.INTER_LINEAR)
        allowed = (np.linalg.norm(self.shifts - prediction, axis=2) <= radius)
        allowed &= (road_area > 1600 * self.scale ** 2) & (grid_area > 12800 * self.scale ** 2)
        match = self.peak(roads + 0.7 * grid, allowed)
        if match is None:
            return None
        delta, (y, x), gap = match
        return {'delta': delta.tolist(), 'road_iou': float(roads[y, x]), 'grid_score': float(grid[y, x]),
                'gap': gap, 'prediction_error_px': float(np.linalg.norm(delta - prediction))}


def solve_joint_positions(positions, moves, constraints, motion_map, cell):
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import lsqr

    # Keep a weak connected odometry chain through views with too little visible road.
    chain = [{'a': i, 'b': i + 1, 'delta': (positions[i] - positions[i + 1]) @ motion_map}
             for i in range(len(positions) - 1)]
    if moves:
        chain = [dict(move, delta=np.asarray(move['delta']) @ motion_map) for move in moves]
    edges = chain + constraints
    a = np.array([e['a'] for e in edges])
    b = np.array([e['b'] for e in edges])
    delta = np.array([e['delta'] for e in edges])
    base = np.r_[np.full(len(chain), 0.08), np.ones(len(constraints))]
    weights = base.copy()
    count = len(edges)
    rows = np.r_[np.arange(count), np.arange(count), count]
    cols = np.r_[a, b, 0]
    corrected = positions @ motion_map
    for _ in range(30):
        system = coo_matrix((np.r_[weights, -weights, 10], (rows, cols)),
                            shape=(count + 1, len(positions))).tocsr()
        targets = np.vstack([delta * weights[:, None], [0, 0]])
        updated = np.column_stack([lsqr(system, targets[:, axis], atol=1e-8, btol=1e-8,
                                        iter_lim=5000)[0] for axis in (0, 1)])
        residual = np.linalg.norm(updated[a] - updated[b] - delta, axis=1)
        weights = base * np.sqrt(np.minimum(1, (cell / 10) / np.maximum(residual, 1e-3)))
        change = np.max(np.linalg.norm(updated - corrected, axis=1))
        corrected = updated
        if change < 0.001:
            break
    return corrected, residual[len(chain):]


def refine_map_positions(output, data, source):
    started = time.perf_counter()
    positions = np.array([f['position'] for f in data['frames']], dtype=float)
    report = {'status': 'insufficient_joint_evidence', 'reason': 'too_few_frames_or_no_strokes'}
    if len(positions) < 16 or not data.get('strokes'):
        return positions, report
    cell = data['cell_px']
    matcher = JointRegistration(np.array(data['matrix']), data['warp_size'], source, cell)
    views = []
    for frame in data['frames']:
        image = cv2.imread(str(output / frame['file']))
        if image is None:
            raise OSError(f"Cannot read scan frame: {frame['file']}")
        views.append(matcher.features(image))
    grid_moves, road_moves = [], []
    candidates = 0
    for i in range(len(views) - 1):
        prediction = positions[i] - positions[i + 1]
        if not cell * 0.7 < np.linalg.norm(prediction) < cell * 3.4:
            continue
        candidates += 1
        roads, _, area, _ = matcher.correlate(views[i], views[i + 1])
        allowed = (np.linalg.norm(matcher.shifts - prediction, axis=2) <= cell * 2)
        allowed &= area > 1600 * matcher.scale ** 2
        peak = matcher.peak(roads, allowed)
        if peak is None:
            continue
        delta, point, _ = peak
        if roads[point] > 0.93 and np.linalg.norm(delta - prediction) < cell * 1.7:
            grid_moves.append(prediction)
            road_moves.append(delta)
    calibration = matcher.fit_motion(grid_moves, road_moves, cell)
    report.update(reason='motion_calibration_failed', calibration_candidates=candidates,
                  calibration_samples=len(grid_moves))
    if calibration is None:
        return positions, report
    motion_map, calibration_report = calibration
    initial = positions @ motion_map
    constraints = []
    for i in range(len(views) - 1):
        road = matcher.measure_roads(views[i], views[i + 1], initial[i] - initial[i + 1], cell * 2)
        if road is not None:
            constraints.append(dict(road, a=i, b=i + 1, kind='adjacent_road'))
            continue
        result = matcher.measure(views[i], views[i + 1], initial[i] - initial[i + 1], motion_map, cell * 2)
        if result and (result['road_iou'] > 0.7 and result['grid_score'] > 0.52
                       and result['prediction_error_px'] < cell / 2):
            constraints.append(dict(result, a=i, b=i + 1, kind='adjacent'))
    print(json.dumps({'joint_registration': 'matching_revisited_roads', 'grid_to_road': motion_map.tolist(),
                      'adjacent_constraints': len(constraints)}), flush=True)
    for i in range(len(views)):
        candidates = [j for j in range(i + 12, len(views)) if np.linalg.norm(initial[i] - initial[j]) < cell * 8.4]
        candidates.sort(key=lambda j: np.linalg.norm(initial[i] - initial[j]))
        selected = []
        for j in candidates:
            if any(abs(j - previous) < 8 for previous in selected):
                continue
            selected.append(j)
            result = matcher.measure(views[i], views[j], initial[i] - initial[j], motion_map, cell * 4)
            if result and result['road_iou'] > 0.86 and result['grid_score'] > 0.52 and result['gap'] > 0.04:
                constraints.append(dict(result, a=i, b=j, kind='loop'))
            if len(selected) == 3:
                break
    if len(constraints) < 12:
        report.update(reason='too_few_constraints', constraints_found=len(constraints))
        return positions, report
    corrected, residual = solve_joint_positions(positions, data['moves'], constraints, motion_map, cell)
    accepted = [edge for edge, error in zip(constraints, residual) if error < cell / 3]
    rejected = len(constraints) - len(accepted)
    if len(accepted) < 12:
        report.update(reason='too_few_consistent_constraints', constraints_found=len(constraints),
                      rejected_constraints=rejected)
        return positions, report
    if rejected:
        corrected, residual = solve_joint_positions(positions, data['moves'], accepted, motion_map, cell)
    report.pop('reason')
    report.update(status='joint_grid_road', grid_to_road=motion_map.tolist(), calibration=calibration_report,
                  adjacent_constraints=sum(e['kind'].startswith('adjacent') for e in accepted),
                  road_constraints=sum(e['kind'] == 'adjacent_road' for e in accepted),
                  loop_constraints=sum(e['kind'] == 'loop' for e in accepted), rejected_constraints=rejected,
                  constraints=accepted, positions=corrected.round(4).tolist(),
                  residual_median_px=float(np.median(residual)) if len(residual) else None,
                  seconds=round(time.perf_counter() - started, 2))
    return corrected, report


def reproject_scan(data, matrix, warp_size, shape):
    """Convert odometry seeds locally; road constraints must then solve the new poses."""
    height, width = shape
    points = np.array([[[width / 2, height / 2], [width / 2 + 1, height / 2],
                        [width / 2, height / 2 + 1]]], float)
    before = cv2.perspectiveTransform(points, np.asarray(data['matrix']))[0]
    after = cv2.perspectiveTransform(points, matrix)[0]
    linear = np.linalg.solve(before[1:] - before[0], after[1:] - after[0])
    converted = copy.deepcopy(data)
    converted.update(matrix=matrix.tolist(), warp_size=list(warp_size))
    for frame in converted['frames']:
        frame['position'] = (np.asarray(frame['position']) @ linear).tolist()
    for move in converted['moves']:
        move['delta'] = (np.asarray(move['delta']) @ linear).tolist()
    return converted, linear


def projection_road_score(data, source, images, pairs):
    matcher = JointRegistration(np.asarray(data['matrix']), data['warp_size'], source, data['cell_px'])
    views = {i: matcher.features(image) for i, image in images.items()}
    positions = np.array([f['position'] for f in data['frames']])
    scores = []
    for a, b in pairs:
        roads, _, area, _ = matcher.correlate(views[a], views[b])
        prediction = positions[a] - positions[b]
        allowed = np.linalg.norm(matcher.shifts - prediction, axis=2) < data['cell_px'] * 2
        allowed &= area > 1600 * matcher.scale ** 2
        peak = matcher.peak(roads, allowed)
        scores.append(float(roads[peak[1]]) if peak is not None else 0.0)
    return float(np.median(scores))


def recover_map_projection(output, data, source):
    """Retry failed calibration using saved views, gated by measured road overlap."""
    positions = np.array([f['position'] for f in data['frames']])
    lengths = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    eligible = np.flatnonzero((lengths > data['cell_px'] * 0.7) & (lengths < data['cell_px'] * 3.4))
    report = {'status': 'rejected', 'reason': 'too_few_moving_pairs'}
    if len(eligible) < 12:
        return None, report
    indices = eligible[np.linspace(0, len(eligible) - 1, 12, dtype=int)]
    pairs = [(int(i), int(i + 1)) for i in indices]
    references = np.linspace(0, len(positions) - 1, 5, dtype=int)
    images = {}
    for i in sorted(set(references) | {i for pair in pairs for i in pair}):
        image = cv2.imread(str(output / data['frames'][i]['file']))
        if image is None:
            raise OSError(f"Cannot read scan frame: {data['frames'][i]['file']}")
        images[i] = image
    baseline = projection_road_score(data, source, images, pairs)
    report.update(reason='no_better_projection', original_road_iou=baseline, candidates=[])
    best = None
    for i in references:
        try:
            grid = MetricGrid(images[i], data['cell_px'])
            candidate, linear = reproject_scan(data, grid.matrix, grid.size, source.shape)
        except (ValueError, np.linalg.LinAlgError) as exc:
            report['candidates'].append({'frame': int(i), 'error': str(exc)})
            continue
        score = projection_road_score(candidate, source, images, pairs)
        report['candidates'].append({'frame': int(i), 'road_iou': score})
        if score < 0.9 or score < baseline + 0.04:
            continue
        if best is None or score > best[0]:
            best = (score, int(i), candidate, linear)
    if best is None:
        return None, report
    score, frame, candidate, linear = best
    corrected, registration = refine_map_positions(output, candidate, source)
    if registration['status'] != 'joint_grid_road':
        report.update(reason='recalibrated_registration_failed', registration=registration)
        return None, report
    report.update(status='accepted', reason='road_overlap_and_joint_constraints_verified',
                  frame=frame, road_iou=score, matrix=candidate['matrix'], warp_size=candidate['warp_size'],
                  odometry_seed_transform=linear.tolist())
    registration['projection_recovery'] = report
    return (candidate, corrected, registration), report


def map_orientation(matrix, warp_size, shape, mode='screen_ne_up'):
    """Orient the rectified map without changing distances or angles."""
    height, width = shape
    center = np.array([width / 2, height / 2], float)
    if mode == 'screen_oblique':
        points = np.array([center, center + [1, 0], center + [0, 1]])
        projected = cv2.perspectiveTransform(points[None], np.asarray(matrix, float))[0]
        jacobian = (projected[1:] - projected[0]).T
        if abs(np.linalg.det(jacobian)) < 1e-8 or np.linalg.cond(jacobian) > 100:
            raise ValueError('Cannot recover the screen map directions from this projection.')
        # Keep only orientation; the inverse projection's shear and scale would reintroduce camera tilt.
        left, _, right = np.linalg.svd(np.linalg.inv(jacobian))
        rotation = left @ right
        corners = np.array([[0, 0], [warp_size[0] - 1, 0],
                            [0, warp_size[1] - 1], [warp_size[0] - 1, warp_size[1] - 1]]) @ rotation.T
        lo, hi = np.floor(corners.min(axis=0)), np.ceil(corners.max(axis=0))
        transform = np.eye(3)
        transform[:2, :2], transform[:2, 2] = rotation, -lo
        return transform, tuple(int(value) for value in hi - lo + 1)
    points = np.array([center, center + [60, -35], center + [60, 35]])
    projected = cv2.perspectiveTransform(points[None], np.asarray(matrix, float))[0]
    north, east = projected[1:] - projected[0]
    north_axis, east_axis = int(np.abs(north).argmax()), int(np.abs(east).argmax())
    if north_axis == east_axis:
        directions = np.array([north, east])
        lengths = np.linalg.norm(directions, axis=1)
        if np.min(lengths) < 1e-6:
            raise ValueError('Grid axes cannot distinguish screen northeast from southeast.')
        normalized = directions / lengths[:, None]
        scores = [abs(normalized[0, 0]) + abs(normalized[1, 1]),
                  abs(normalized[0, 1]) + abs(normalized[1, 0])]
        if abs(np.linalg.det(normalized)) < 0.2 or abs(scores[0] - scores[1]) < 0.1:
            raise ValueError('Grid axes cannot distinguish screen northeast from southeast.')
        # Perspective can make both directions favor one axis; choose a unique axis pairing jointly.
        north_axis = int(scores[1] > scores[0])
        east_axis = 1 - north_axis
    rotation = np.zeros((2, 2))
    rotation[0, east_axis] = np.sign(east[east_axis])
    rotation[1, north_axis] = -np.sign(north[north_axis])
    corners = np.array([[0, 0], [warp_size[0] - 1, 0],
                        [0, warp_size[1] - 1], [warp_size[0] - 1, warp_size[1] - 1]]) @ rotation.T
    transform = np.eye(3)
    transform[:2, :2] = rotation
    transform[:2, 2] = -corners.min(axis=0)
    size = tuple(int(value) for value in corners.max(axis=0) - corners.min(axis=0) + 1)
    return transform, size


def rebuild_v2(output, data, registration='joint'):
    matrix = np.array(data['matrix'])
    warp_size = tuple(data['warp_size'])
    source = np.full((data['roi'][3] - data['roi'][1], data['roi'][2] - data['roi'][0]), 255, np.uint8)
    source[:3] = source[-3:] = 0
    source[:, :3] = source[:, -3:] = 0
    source[-35:, -75:] = 0
    valid = cv2.warpPerspective(source, matrix, warp_size, flags=cv2.INTER_NEAREST)
    positions = np.array([f['position'] for f in data['frames']])
    registration_report = {'status': 'odometry'}
    if registration == 'joint':
        positions, registration_report = refine_map_positions(output, data, source)
        if registration_report['status'] == 'insufficient_joint_evidence':
            initial_report = registration_report.copy()
            recovered, recovery_report = recover_map_projection(output, data, source)
            if recovered is not None:
                data, positions, registration_report = recovered
                matrix = np.asarray(data['matrix'])
                warp_size = tuple(data['warp_size'])
                registration_report['original_registration'] = initial_report
            else:
                registration_report['projection_recovery'] = recovery_report
                print('WARNING: Road registration failed; reconstruction is an unverified odometry preview. '
                      f"Reason: {registration_report['reason']}", flush=True)
    if registration_report['status'] != 'joint_grid_road' and len(data['moves']) >= 2:
        from scipy.sparse import coo_matrix
        from scipy.sparse.linalg import lsqr

        rows, cols, values, targets = [], [], [], []
        for row, move in enumerate(data['moves']):
            weight = max(0.05, float(move['corr']))
            rows.extend([row, row])
            cols.extend([move['a'], move['b']])
            values.extend([-weight, weight])
            targets.append(-np.array(move['delta']) * weight)
        rows.append(len(targets))
        cols.append(0)
        values.append(10.0)
        targets.append(np.zeros(2))
        system = coo_matrix((values, (rows, cols)), shape=(len(targets), len(positions))).tocsr()
        targets = np.array(targets)
        for axis in (0, 1):
            positions[:, axis] = lsqr(system, targets[:, axis], atol=1e-7, btol=1e-7, iter_lim=5000)[0]
    display_orientation = data.get('display_orientation', 'screen_ne_up')
    orientation, warp_size = map_orientation(matrix, warp_size, source.shape, display_orientation)
    matrix = orientation @ matrix
    positions = positions @ orientation[:2, :2].T
    valid = cv2.warpPerspective(source, matrix, warp_size, flags=cv2.INTER_NEAREST)
    lo = np.floor(positions.min(axis=0)).astype(int) - 20
    hi = np.ceil(positions.max(axis=0)).astype(int) + np.array(warp_size) + 20
    size = tuple(int(v) for v in hi - lo)
    if max(size) > 16000 or size[0] * size[1] > 60_000_000:
        raise RuntimeError(f'Implausible canvas {size}; inspect failed registrations instead of allocating it.')
    coverage = np.zeros(size[::-1], np.float32)
    roads = np.zeros_like(coverage)
    observed = np.zeros((*size[::-1], 3), np.uint8)
    best_weight = np.zeros_like(coverage)
    center_weight = cv2.distanceTransform(valid, cv2.DIST_L2, 3)
    source_frames = np.full(size[::-1], -1, np.int32)
    enemy_observations = []
    player_observations = []
    player = None
    mosaic_frames = 0
    for frame, position in zip(data['frames'], positions):
        if data.get('mosaic_frame_kinds') and frame['kind'] not in data['mosaic_frame_kinds']:
            continue
        mosaic_frames += 1
        image = cv2.imread(str(output / frame['file']))
        mask = cv2.warpPerspective(terrain(image), matrix, warp_size, flags=cv2.INTER_NEAREST)
        transform = np.float32([[1, 0, position[0] - lo[0]], [0, 1, position[1] - lo[1]]])
        weight = cv2.warpAffine(center_weight, transform, size)
        coverage += cv2.warpAffine(valid.astype(np.float32) / 255, transform, size)
        roads += cv2.warpAffine(mask.astype(np.float32) / 255, transform, size)
        color = cv2.warpAffine(cv2.warpPerspective(image, matrix, warp_size), transform, size)
        select = weight > best_weight
        observed[select] = color[select]
        best_weight[select] = weight[select]
        source_frames[select] = frame['id']
        if frame['kind'] == 'untracked':
            continue
        players, enemies = detect_markers(image, matrix)
        if len(players) == 1:
            candidate = {'position': (np.asarray(players[0]) + position - lo).round(1).tolist(),
                         'frame': frame['id']}
            if player is None:
                player = candidate
            if np.linalg.norm(np.asarray(candidate['position']) - player['position']) < data['cell_px'] * 4:
                player_observations.append(candidate)
        for enemy in enemies:
            enemy_observations.append({'position': (np.asarray(enemy) + position - lo).tolist(),
                                       'frame': frame['id']})
    probability = roads / np.maximum(coverage, 1e-6)
    crop = terrain_crop_bounds(probability, data['cell_px'])
    left, top, right, bottom = crop['box']
    probability = probability[top:bottom, left:right]
    coverage = coverage[top:bottom, left:right]
    observed = observed[top:bottom, left:right]
    source_frames = source_frames[top:bottom, left:right]
    lo += (left, top)
    size = (right - left, bottom - top)
    for observation in player_observations + enemy_observations:
        observation['position'] = (np.asarray(observation['position']) - (left, top)).tolist()
    if player is not None:
        x, y = player['position']
        if not (0 <= x < size[0] and 0 <= y < size[1]):
            player = None
    canvas = np.full((*size[::-1], 3), (15, 20, 28), np.uint8)
    canvas[coverage > 0.5] = (30, 43, 55)
    canvas[(probability > 0.15) & (probability < 0.8)] = (86, 108, 128)
    canvas[probability >= 0.5] = (45, 141, 199)
    Image.fromarray(canvas).save(output / 'reconstruction.png')
    cv2.imwrite(str(output / 'observed_mosaic.png'), observed)
    # Use the same view as the mosaic; other views may have accumulated pose drift.
    for observation in player_observations:
        if marker_contributes(observation, source_frames, data['cell_px'] * 0.35):
            player = observation
            break
    visible_enemies = []
    for observation in enemy_observations:
        if marker_contributes(observation, source_frames, data['cell_px'] * 0.35):
            visible_enemies.append(observation)
    enemies = merge_enemy_markers(visible_enemies, data['cell_px'] * 0.75)
    cv2.imwrite(str(output / 'annotated_mosaic.png'), annotate_markers(observed, player, enemies))
    cv2.imwrite(str(output / 'annotated_reconstruction.png'),
                annotate_markers(cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR), player, enemies))
    (output / 'markers.json').write_text(json.dumps({
        'coordinate_system': 'mosaic pixels; origin at top-left, x right, y down',
        'orientation': display_orientation,
        'self': player, 'enemies': enemies,
        'note': 'Observed marker locations inherit the reconstruction registration error.',
    }, indent=2), encoding='utf-8')
    cv2.imwrite(str(output / 'coverage.png'), np.uint8(np.minimum(coverage, 10) * 25.5))
    cv2.imwrite(str(output / 'terrain_mask.png'), np.uint8(probability >= 0.5) * 255)
    np.savez_compressed(output / 'map_data.npz', coverage=coverage, terrain_probability=probability,
                        positions=positions, origin=lo, projection=matrix,
                        warp_size=warp_size, scan_to_map=orientation,
                        enemies=np.array([item['position'] for item in enemies]).reshape(-1, 2),
                        player=np.array([] if player is None else [player['position']]).reshape(-1, 2))
    summary = {
        'status': data['status'], 'frames': len(data['frames']), 'canvas_size': size,
        'crop': crop,
        'orientation': display_orientation, 'scan_to_map': orientation.tolist(), 'warp_size': warp_size,
        'capture_mode': data.get('capture_mode', 'legacy_live_keyframes'),
        'mosaic_frames': mosaic_frames,
        'mosaic_frame_kinds': data.get('mosaic_frame_kinds', 'all'),
        'localized_frames': len(data['frames']), 'excluded_unlocalized_frames': [],
        'confirmed_clamps': data.get('clamps', []),
        'self_marker': player, 'enemy_markers': len(enemies),
        'registration': {k: v for k, v in registration_report.items() if k not in ('constraints', 'positions')},
        'terrain_edges': data.get('terrain_edges', []),
        'unresolved_frontiers': data.get('unresolved_frontiers', []),
        'coverage_definition': data['coverage_definition'],
        'whole_camera_domain_verified': data['status'] == 'raster_closed',
        'boundary_note': ('Serpentine covered the domain between camera clamps on all four axes.'
                          if data['status'] == 'raster_closed' else
                          'Partial coverage; inspect clamps and frames before trusting the map.'),
    }
    registration_report['coordinate_system'] = 'reconstruction axes, before scan_to_map'
    if registration_report.get('projection_recovery', {}).get('status') == 'accepted':
        summary['scan_evidence_coordinate_system'] = 'clamps and frontiers retain original scan.json axes'
    (output / 'registration.json').write_text(json.dumps(registration_report, indent=2), encoding='utf-8')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--driver-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--roi', nargs=4, type=int, default=[644, 280, 1130, 742])
    parser.add_argument('--client', nargs=2, type=int, default=[1776, 999])
    parser.add_argument('--map-open', nargs=2, type=int, default=[42, 98],
                        help='Compact minimap expand button in client coordinates.')
    parser.add_argument('--step', type=int, default=180)
    parser.add_argument('--settle', type=float, default=1.5)
    parser.add_argument('--horizon', type=float, help='Grid vanishing-line y relative to ROI; negative.')
    parser.add_argument('--axis-slope', type=float, default=0.56, help='Ground-grid slope after rectification.')
    parser.add_argument('--max-axis-steps', type=int, default=80)
    parser.add_argument('--max-frames', type=int, default=1600)
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('--stop-file', type=Path, help='Additional batch-wide stop sentinel.')
    parser.add_argument('--registration', choices=('joint', 'odometry'), default='joint',
                        help='Use perspective-grid/road constraints during rebuild, or retain the recorded odometry.')
    parser.add_argument('--orientation', choices=('screen_oblique', 'screen_ne_up'), default='screen_oblique',
                        help='Display the map in its screen directions, or align screen northeast upward.')
    parser.add_argument('--strategy', choices=('drift', 'grid', 'terrain', 'raster'), default='drift')
    parser.add_argument('--cell-px', type=float, default=48.0, help='Rectified grid cell size in pixels.')
    parser.add_argument('--grid-step', type=float, default=240.0, help='Raster spacing in rectified pixels.')
    parser.add_argument('--min-swipe', type=float, default=30.0, help='Below this screen length the game ignores drags.')
    parser.add_argument('--max-swipe', type=float, default=300.0, help='Keep swipe endpoints inside the safe interior.')
    parser.add_argument('--capture-px', type=float, default=25.0, help='Maximum target cursor travel between captures.')
    parser.add_argument('--capture-interval', type=float, default=0.03)
    parser.add_argument('--drag-speed', type=float, default=420.0,
                        help='Continuous cursor speed in screen pixels/second.')
    parser.add_argument('--keyframe-px', type=float, default=80.0, help='Measured camera travel between saved views.')
    parser.add_argument('--stroke-px', type=float, default=240.0, help='Cursor travel per press-drag stroke.')
    parser.add_argument('--max-world', type=float, default=12000.0, help='Segment length cap in rectified pixels.')
    args = parser.parse_args(argv)
    if min(*args.client, args.capture_px, args.capture_interval, args.drag_speed, args.keyframe_px,
           args.stroke_px, args.max_world, args.grid_step, args.max_frames, args.max_axis_steps) <= 0:
        parser.error('Client size, capture, movement, and budget arguments must be positive.')
    args.output = args.output.resolve()
    args.driver_root = args.driver_root.resolve()
    return args


def main():
    args = parse_args()
    if args.rebuild:
        rebuild(args.output, registration=args.registration)
        return
    if (args.output / 'scan.json').exists():
        raise ValueError('Output already contains a scan; use a new directory or --rebuild.')
    window = DriverWindow(args)
    scanner = None
    try:
        window.reset_minimap()
        if args.strategy == 'drift':
            scanner = DriftScanner(args, window)
            scanner.explore_drift()
        elif args.strategy == 'grid':
            scanner = RasterScanner(args, window)
            scanner.explore_frontier()
        else:
            scanner = Scanner(args, window)
            if args.strategy == 'terrain':
                scanner.explore_terrain()
            else:
                scanner.explore()
    except (RuntimeError, ValueError, OSError, KeyboardInterrupt) as exc:
        if scanner is not None:
            scanner.data['status'] = 'incomplete'
            scanner.data['stop_reason'] = str(exc)
            scanner.save()
            rebuild(args.output, registration=args.registration)
        raise
    finally:
        window.close()
    rebuild(args.output, registration=args.registration)


if __name__ == '__main__':
    main()
