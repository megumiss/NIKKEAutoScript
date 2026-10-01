import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from dev_tools.minimap_reconstruct import (
    DriverWindow, DriftScanner, FlowTracker, JointRegistration, MetricGrid, detect_markers, marker_contributes,
    grid_horizon_directions, map_orientation, merge_enemy_markers, recover_map_projection, reproject_scan,
    solve_joint_positions,
    stationary_stroke, stationary_tail,
    rebuild_v2, terrain_crop_bounds,
)


class ClientSizeTests(unittest.TestCase):
    def setUp(self):
        self.window = DriverWindow.__new__(DriverWindow)
        self.window.args = SimpleNamespace(client=[1776, 999])
        self.window.hwnd = 1
        self.window.gui = Mock()
        self.window.gui.IsIconic.return_value = False
        self.window.gui.GetWindowPlacement.return_value = (0, 1, (0, 0), (0, 0), (0, 0, 1800, 1100))
        self.window.gui.GetClientRect.return_value = (0, 0, 1766, 993)
        self.window.gui.GetWindowRect.return_value = (5389, 412, 7177, 1461)
        self.window.gui.ClientToScreen.return_value = (5400, 457)
        self.api = Mock()
        self.api.GetMonitorInfo.return_value = {'Work': (3840, 0, 7680, 2088)}
        constants = SimpleNamespace(SW_RESTORE=9, SW_SHOWMAXIMIZED=3, MONITOR_DEFAULTTONEAREST=2,
                                    SWP_NOZORDER=4, SWP_NOACTIVATE=16)
        modules = patch.dict(sys.modules, {'win32api': self.api, 'win32con': constants,
                                         'pywintypes': SimpleNamespace(error=OSError)})
        modules.start()
        self.addCleanup(modules.stop)
        sleep = patch('dev_tools.minimap_reconstruct.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_resizes_client_with_borders_on_current_monitor(self):
        def apply_size(hwnd, after, x, y, width, height, flags):
            self.window.gui.GetClientRect.return_value = (0, 0, width - 22, height - 56)
        self.window.gui.SetWindowPos.side_effect = apply_size
        self.window.ensure_client_size()
        self.window.gui.SetWindowPos.assert_called_once_with(1, 0, 5389, 412, 1798, 1055, 20)
        self.window.check()

    def test_correct_client_needs_no_resize(self):
        self.window.gui.GetClientRect.return_value = (0, 0, 1776, 999)
        self.window.ensure_client_size()
        self.window.gui.SetWindowPos.assert_not_called()

    def test_minimized_window_is_restored_and_clamped_inside_monitor(self):
        self.window.gui.IsIconic.return_value = True
        self.window.gui.GetClientRect.return_value = (0, 0, 1776, 999)
        self.window.gui.GetWindowRect.return_value = (7000, 1600, 8798, 2655)
        self.window.gui.ClientToScreen.return_value = (7011, 1645)
        self.window.ensure_client_size()
        self.window.gui.ShowWindow.assert_called_once_with(1, 9)
        self.window.gui.SetWindowPos.assert_called_once_with(1, 0, 5882, 1033, 1798, 1055, 20)

    def test_reverted_size_retries_then_stops(self):
        self.window.gui.GetClientRect.side_effect = [(0, 0, 1766, 993), (0, 0, 1766, 993),
                                                    (0, 0, 1776, 999), (0, 0, 1766, 993)] + [
                                                        (0, 0, 1766, 993)] * 5
        with self.assertRaisesRegex(RuntimeError, 'did not stabilize'):
            self.window.ensure_client_size()
        self.assertEqual(self.window.gui.SetWindowPos.call_count, 3)

    def test_small_monitor_stops_before_resizing(self):
        self.api.GetMonitorInfo.return_value = {'Work': (0, 0, 1280, 720)}
        with self.assertRaisesRegex(RuntimeError, 'does not fit'):
            self.window.ensure_client_size()
        self.window.gui.SetWindowPos.assert_not_called()

    def test_1080p_allows_bottom_border_overlap_while_keeping_client_visible(self):
        for work_top in (0, 48):
            with self.subTest(work_top=work_top):
                self.api.GetMonitorInfo.return_value = {'Work': (3840, work_top, 5760, work_top + 1032)}
                self.window.gui.GetClientRect.return_value = (0, 0, 1183, 664)
                self.window.gui.GetWindowRect.return_value = (4337, 290, 5536, 993)
                self.window.gui.ClientToScreen.return_value = (4345, 321)

                def apply_size(hwnd, after, x, y, width, height, flags):
                    self.window.gui.GetClientRect.return_value = (0, 0, width - 16, height - 39)
                    self.window.gui.GetWindowRect.return_value = (x, y, x + width, y + height)
                    self.window.gui.ClientToScreen.return_value = (x + 8, y + 31)

                self.window.gui.SetWindowPos.side_effect = apply_size
                self.window.ensure_client_size()
                self.assertEqual(self.window.gui.GetClientRect.return_value, (0, 0, 1776, 999))
                left, top, right, bottom = self.window.gui.GetWindowRect.return_value
                _, client_y = self.window.gui.ClientToScreen.return_value
                self.assertEqual(top, work_top)
                self.assertGreaterEqual(left, 3840)
                self.assertLessEqual(right, 5760)
                self.assertEqual(client_y + 999, work_top + 1030)
                self.assertEqual(bottom, work_top + 1038)

    def test_1080p_accepts_existing_client_at_top_without_resizing(self):
        self.api.GetMonitorInfo.return_value = {'Work': (3840, 0, 5760, 1032)}
        self.window.gui.GetClientRect.return_value = (0, 0, 1776, 999)
        self.window.gui.GetWindowRect.return_value = (3904, 0, 5696, 1038)
        self.window.gui.ClientToScreen.return_value = (3912, 31)
        self.window.ensure_client_size()
        self.window.gui.SetWindowPos.assert_not_called()

    def test_client_or_side_border_outside_work_area_still_stops(self):
        self.window.gui.GetClientRect.return_value = (0, 0, 1776, 999)
        self.window.gui.GetWindowRect.return_value = (3904, 0, 5696, 1038)
        self.window.gui.ClientToScreen.return_value = (3912, 31)
        for width, height in ((1920, 1029), (1791, 1032)):
            with self.subTest(width=width, height=height):
                self.api.GetMonitorInfo.return_value = {'Work': (3840, 0, 3840 + width, height)}
                with self.assertRaisesRegex(RuntimeError, 'does not fit'):
                    self.window.ensure_client_size()
                self.window.gui.SetWindowPos.assert_not_called()

    def test_windows_permission_error_is_reported_without_retrying_input(self):
        self.window.gui.SetWindowPos.side_effect = OSError(5, 'Access denied')
        with self.assertRaisesRegex(RuntimeError, 'same permissions as NIKKE'):
            self.window.ensure_client_size()
        self.window.gui.SetWindowPos.assert_called_once()


class TerrainCropTests(unittest.TestCase):
    def test_preserves_separate_roads_and_faint_edges(self):
        probability = np.zeros((400, 600), np.float32)
        probability[150:250, 200:400] = 1
        probability[70:75, 510:515] = 0.2
        probability[2, 2] = 0.1
        self.assertEqual(terrain_crop_bounds(probability)['box'], [152, 22, 563, 298])

    def test_each_canvas_uses_its_own_extents_and_clamps_padding(self):
        for height, width in [(80, 120), (900, 300)]:
            probability = np.zeros((height, width), np.float32)
            probability[0:10, width - 10:width] = 1
            self.assertEqual(terrain_crop_bounds(probability)['box'], [width - 58, 0, width, 58])

    def test_empty_map_retains_diagnostic_canvas(self):
        crop = terrain_crop_bounds(np.zeros((150, 300)))
        self.assertEqual(crop['box'], [0, 0, 300, 150])
        self.assertEqual(crop['basis'], 'no_terrain_keep_canvas')

    def test_rebuild_and_static_export_keep_images_cache_and_markers_aligned(self):
        import dev_tools.minimap_reconstruct as reconstruct
        with patch.dict(sys.modules, {'minimap_reconstruct': reconstruct}):
            from dev_tools.minimap_chapters import export_static
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            source = package / 'source'
            source.mkdir()
            image = np.zeros((120, 160, 3), np.uint8)
            image[40:80, 45:95] = (200, 130, 20)
            # A blue control inside the excluded corner must not become a floating road fragment.
            image[100:115, 120:155] = (200, 130, 20)
            cv2.imwrite(str(source / 'frame.png'), image)
            positions = np.array([[0.0, 0.0], [30.0, 0.0]])
            data = {'matrix': np.eye(3).tolist(), 'warp_size': [160, 120], 'roi': [0, 0, 160, 120],
                    'cell_px': 12, 'moves': [], 'status': 'roads_exhausted', 'display_orientation': 'screen_oblique',
                    'coverage_definition': 'test', 'frames': [
                        {'id': i, 'position': position.tolist(), 'file': 'frame.png', 'kind': 'node'}
                        for i, position in enumerate(positions)]}
            with patch.object(reconstruct, 'refine_map_positions', return_value=(
                    positions, {'status': 'joint_grid_road'})), patch.object(
                        reconstruct, 'map_orientation', return_value=(np.eye(3), (160, 120))), patch.object(
                            reconstruct, 'detect_markers', return_value=([(60, 60)], [(80, 60)])):
                rebuild_v2(source, data)
            summary = export_static(package, 1)
            crop = summary['crop']
            self.assertEqual(crop['box'], [53, 48, 157, 112])
            self.assertEqual(summary['canvas_size'], [104, 64])
            with np.load(source / 'map_data.npz') as cache:
                np.testing.assert_array_equal(cache['origin'], [33, 28])
                np.testing.assert_array_equal(cache['positions'], positions)
                self.assertEqual(cache['terrain_probability'].shape, (64, 104))
                for name in ['reconstruction.png', 'observed_mosaic.png', 'annotated_reconstruction.png',
                             'annotated_mosaic.png', 'coverage.png', 'terrain_mask.png']:
                    self.assertEqual(cv2.imread(str(source / name)).shape[:2], (64, 104))
                markers = json.loads((source / 'markers.json').read_text())
                for marker in [markers['self']] + markers['enemies']:
                    x, y = marker['position']
                    self.assertTrue(0 <= x < 104 and 0 <= y < 64)
                np.testing.assert_allclose(cache['player'][0], markers['self']['position'])
                metadata = json.loads((package / 'map.json').read_text())
                np.testing.assert_allclose(metadata['transforms']['origin'], cache['origin'])
                point = np.array([60.0, 60.0])
                np.testing.assert_allclose(point + positions[0] - cache['origin'], [27, 32])
            self.assertEqual(metadata['crop'], crop)
            digest = hashlib.sha256((package / 'map.png').read_bytes()).hexdigest()
            annotations_path = package / 'annotations.json'
            annotations = json.loads(annotations_path.read_text())
            self.assertEqual(digest, metadata['image_sha256'])
            self.assertEqual(digest, annotations['image_sha256'])
            for name in ['map.png', 'reference.png']:
                self.assertEqual(cv2.imread(str(package / name)).shape[:2], (64, 104))
            annotations['objects'] = [{'id': 'keep'}]
            annotations_path.write_text(json.dumps(annotations))
            with self.assertRaisesRegex(RuntimeError, 'preserve its annotation'):
                export_static(package, 1)
            self.assertEqual(json.loads(annotations_path.read_text()), annotations)


class OrientationTests(unittest.TestCase):
    def test_oblique_display_preserves_metric_geometry_and_contains_corners(self):
        matrix = np.array([[-1.8, 0.55, 903], [0.29, -1.46, 717], [0.0001, 0.0024, 1]])
        transform, size = map_orientation(matrix, (1000, 900), (462, 486), 'screen_oblique')
        points = np.array([[[243, 231], [244, 231], [243, 232]]], float)
        mapped = cv2.perspectiveTransform(points, transform @ matrix)[0]
        directions = mapped[1:] - mapped[0]
        self.assertGreater(directions[0, 0], 0)
        self.assertGreater(directions[1, 1], 0)
        rotation = transform[:2, :2]
        np.testing.assert_allclose(rotation.T @ rotation, np.eye(2), atol=1e-8)
        square = np.array([[0, 0], [100, 0], [100, 100], [0, 100]]) @ rotation.T
        edges = np.roll(square, -1, axis=0) - square
        np.testing.assert_allclose(np.linalg.norm(edges, axis=1), 100, atol=1e-8)
        self.assertAlmostEqual(float(edges[0] @ edges[1]), 0)
        corners = np.array([[[0, 0], [999, 0], [0, 899], [999, 899]]], float)
        mapped = cv2.perspectiveTransform(corners, transform)[0]
        self.assertTrue(np.all(mapped >= 0))
        self.assertTrue(np.all(mapped <= np.array(size) - 1))

    def test_perspective_pairs_axes_jointly(self):
        matrix = np.array([[-1.81538, 0.55781, 903.20454],
                           [0.29041, -1.46258, 717.46114],
                           [0.00009558, 0.00235373, 1]])
        transform, _ = map_orientation(matrix, (1000, 900), (462, 486))
        np.testing.assert_array_equal(transform[:2, :2], [[-1, 0], [0, -1]])
        points = np.array([[[243, 231], [303, 196], [303, 266]]], float)
        mapped = cv2.perspectiveTransform(points, transform @ matrix)[0]
        self.assertLess(mapped[1, 1], mapped[0, 1])
        self.assertGreater(mapped[2, 0], mapped[0, 0])

    def test_ambiguous_axis_pairing_is_rejected(self):
        matrix = np.array([[1, 0, 0], [0, 0.01, 0], [0, 0, 1]], float)
        with self.assertRaises(ValueError):
            map_orientation(matrix, (1000, 900), (462, 486))

    def test_all_signed_axis_orders_put_screen_northeast_up(self):
        base = np.array([[1, 1, 20], [-1, 1, 320], [0, 0, 1]], float)
        center = np.array([[243, 231], [303, 196], [303, 266]], float)
        for swap in (False, True):
            for sx in (-1, 1):
                for sy in (-1, 1):
                    perm = np.eye(3)
                    perm[:2, :2] = np.array([[0, sx], [sy, 0]]) if swap else np.diag([sx, sy])
                    matrix = perm @ base
                    transform, size = map_orientation(matrix, (1000, 900), (462, 486))
                    self.assertEqual(json.loads(json.dumps(size)), list(size))
                    points = cv2.perspectiveTransform(center[None], transform @ matrix)[0]
                    north, east = points[1:] - points[0]
                    self.assertLess(north[1], 0)
                    self.assertGreater(abs(north[1]), abs(north[0]))
                    self.assertGreater(east[0], 0)
                    self.assertGreater(abs(east[0]), abs(east[1]))
                    corners = np.array([[[0, 0], [999, 899]]], float)
                    mapped = cv2.perspectiveTransform(corners, transform)[0]
                    np.testing.assert_allclose(mapped.min(axis=0), [0, 0])
                    np.testing.assert_allclose(mapped.max(axis=0), np.array(size) - 1)

    def test_relative_camera_positions_follow_the_same_orientation(self):
        matrix = np.array([[-1, 1, 300], [1, 1, 20], [0, 0, 1]], float)
        transform, _ = map_orientation(matrix, (1000, 900), (462, 486))
        pixel = np.array([200, 200, 1.0])
        position = np.array([70, -120])
        expected = transform @ np.r_[(matrix @ pixel)[:2] + position, 1]
        actual = (transform @ matrix @ pixel)[:2] + transform[:2, :2] @ position
        np.testing.assert_allclose(actual, expected[:2])


class MarkerTests(unittest.TestCase):
    def test_projects_player_ring_and_pink_enemy_without_counting_controls(self):
        image = np.full((462, 486, 3), (65, 40, 20), np.uint8)
        cv2.circle(image, (200, 200), 22, (240, 240, 240), 2)
        cv2.fillPoly(image, [np.array([[280, 300], [310, 300], [295, 330]])], (190, 135, 245))
        cv2.putText(image, '0/14', (420, 450), 0, 0.6, (255, 255, 255), 2)
        matrix = np.array([[2.0, 0, 10], [0, 2.0, 20], [0, 0, 1]])
        players, enemies = detect_markers(image, matrix)
        self.assertEqual(len(players), 1)
        self.assertEqual(len(enemies), 1)
        np.testing.assert_allclose(players[0], [410, 420], atol=4)
        np.testing.assert_allclose(enemies[0], [600, 640], atol=2)

    def test_click_diamond_and_clipped_enemy_are_not_markers(self):
        image = np.zeros((462, 486, 3), np.uint8)
        cv2.polylines(image, [np.array([[200, 100], [245, 145], [200, 190], [155, 145]])],
                      True, (255, 255, 255), 4)
        cv2.fillPoly(image, [np.array([[0, 300], [20, 300], [0, 335]])], (190, 135, 245))
        players, enemies = detect_markers(image, np.eye(3))
        self.assertEqual(players, [])
        self.assertEqual(enemies, [])

    def test_deduplicates_views_without_merging_adjacent_enemies_in_one_frame(self):
        observations = [
            {'frame': 0, 'position': [100, 100]}, {'frame': 0, 'position': [125, 100]},
            {'frame': 1, 'position': [102, 101]}, {'frame': 1, 'position': [126, 101]},
        ]
        groups = merge_enemy_markers(observations, 36)
        self.assertEqual(len(groups), 2)
        self.assertEqual([group['observations'] for group in groups], [2, 2])

    def test_seam_does_not_drop_symbol_whose_center_comes_from_another_view(self):
        sources = np.zeros((100, 100), np.int32)
        sources[:, 50:] = 1
        observations = [{'frame': 0, 'position': [53, 50]}, {'frame': 1, 'position': [47, 50]}]
        visible = [item for item in observations if marker_contributes(item, sources, 15)]
        self.assertEqual(len(visible), 2)
        self.assertEqual(len(merge_enemy_markers(visible, 36)), 1)

    def test_unselected_view_and_outside_canvas_are_not_visible_markers(self):
        sources = np.zeros((100, 100), np.int32)
        for observation in [{'frame': 1, 'position': [50, 50]}, {'frame': 0, 'position': [-1, 50]}]:
            self.assertFalse(marker_contributes(observation, sources, 15))


class MinimapResetTests(unittest.TestCase):
    def setUp(self):
        self.blue = np.full((462, 486, 3), (160, 100, 30), np.uint8)
        self.blank = np.zeros_like(self.blue)
        self.compact_panel = np.full((218, 208, 3), (30, 100, 160), np.uint8)
        self.closed_panel = np.zeros_like(self.compact_panel)
        cv2.circle(self.closed_panel, (22, 22), 10, (240, 240, 240), 2)
        self.compact_panel[:44, :44] = self.closed_panel[:44, :44]
        self.expanded_panel = np.zeros_like(self.compact_panel)

    def window(self, images):
        window = DriverWindow.__new__(DriverWindow)
        window.focus = Mock()
        window.check = Mock()
        window.gui = Mock()
        window.gui.GetForegroundWindow.return_value = 1
        window.gui.ClientToScreen.return_value = (100, 200)
        window.hwnd = 1
        window.args = SimpleNamespace(map_open=[42, 98])
        window.roi = (644, 280, 1130, 742)
        window.handler = Mock(_failures=0)
        window.capture = Mock(side_effect=images)
        return window

    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_expanded_map_is_minimized_then_reopened(self, grab, sleep):
        grab.side_effect = [self.expanded_panel, self.compact_panel, self.expanded_panel]
        window = self.window([self.blue, self.blank, self.blue])
        window.reset_minimap()
        self.assertEqual([call.args for call in window.handler.mouse_click.call_args_list],
                         [(758, 469), (142, 298)])

    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_compact_map_is_expanded_directly(self, grab, sleep):
        grab.side_effect = [self.compact_panel, self.expanded_panel]
        window = self.window([self.blank, self.blue])
        window.reset_minimap()
        self.assertEqual([call.args for call in window.handler.mouse_click.call_args_list],
                         [(142, 298)])

    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_return_to_compact_uses_upper_left_minimize(self, grab, sleep):
        grab.side_effect = [self.expanded_panel, self.compact_panel]
        window = self.window([self.blue, self.blank])
        window.reset_minimap(expanded=False)
        window.handler.mouse_click.assert_called_once_with(758, 469)

    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_closed_map_is_opened_and_expanded(self, grab, sleep):
        grab.side_effect = [self.closed_panel, self.compact_panel, self.expanded_panel]
        window = self.window([self.blank, self.blank, self.blue])
        window.reset_minimap()
        self.assertEqual([call.args for call in window.handler.mouse_click.call_args_list],
                         [(142, 298), (142, 298)])

    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_other_page_does_not_receive_an_open_click(self, grab, sleep):
        grab.return_value = np.zeros_like(self.compact_panel)
        window = self.window([self.blank])
        with self.assertRaisesRegex(RuntimeError, 'Minimap controls are not visible'):
            window.reset_minimap()
        window.handler.mouse_click.assert_not_called()

    @patch('dev_tools.minimap_reconstruct.time.monotonic', side_effect=[0, 0, 4])
    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_failed_minimize_stops_without_sending_an_open_click(self, grab, sleep, monotonic):
        grab.return_value = self.expanded_panel
        window = self.window([self.blue, self.blue])
        with self.assertRaisesRegex(RuntimeError, 'did not reach compact.*observed expanded'):
            window.reset_minimap()
        window.handler.mouse_click.assert_called_once_with(758, 469)

    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_blue_scene_with_compact_controls_is_not_minimized(self, grab, sleep):
        grab.side_effect = [self.compact_panel, self.expanded_panel]
        window = self.window([self.blue, self.blue])
        window.reset_minimap()
        window.handler.mouse_click.assert_called_once_with(142, 298)

    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_road_under_compact_icon_does_not_require_dark_background(self, grab, sleep):
        panel = self.compact_panel.copy()
        panel[:44, :44] = (30, 100, 160)
        cv2.circle(panel, (22, 22), 10, (240, 240, 240), 2)
        grab.side_effect = [panel, self.expanded_panel]
        window = self.window([self.blank, self.blue])
        window.reset_minimap()
        window.handler.mouse_click.assert_called_once_with(142, 298)

    @patch('dev_tools.minimap_reconstruct.time.monotonic', side_effect=[0, 0, 4, 4, 4])
    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_missed_minimize_retries_only_after_confirming_the_control(self, grab, sleep, monotonic):
        grab.side_effect = [self.expanded_panel, self.expanded_panel,
                            self.closed_panel[:44, :44], self.compact_panel]
        window = self.window([self.blue, self.blue, self.blank])
        window.reset_minimap(expanded=False, reset=False)
        self.assertEqual([call.args for call in window.handler.mouse_click.call_args_list],
                         [(758, 469), (758, 469)])

    @patch('dev_tools.minimap_reconstruct.time.monotonic', side_effect=[0, 0, 4])
    @patch('dev_tools.minimap_reconstruct.time.sleep')
    @patch('PIL.ImageGrab.grab')
    def test_changed_page_after_missed_toggle_never_receives_retry(self, grab, sleep, monotonic):
        grab.side_effect = [self.expanded_panel, np.zeros_like(self.compact_panel)]
        window = self.window([self.blue, self.blank])
        with self.assertRaisesRegex(RuntimeError, 'observed unknown'):
            window.reset_minimap(expanded=False)
        window.handler.mouse_click.assert_called_once_with(758, 469)

    def test_control_shape_survives_additional_bright_background(self):
        icon = self.closed_panel[:44, :44].copy()
        icon[:9] = 255
        icon[-7:] = 255
        self.assertTrue(DriverWindow.minimap_control_visible(icon))
        self.assertFalse(DriverWindow.minimap_control_visible(np.full_like(icon, 255)))

    def test_ch48_control_with_multiple_inner_contours(self):
        fixture = Path(__file__).parent / 'fixtures/minimap_control/ch48_multiple_holes.png'
        icon = cv2.imread(str(fixture))
        self.assertIsNotNone(icon)
        self.assertTrue(DriverWindow.minimap_control_visible(icon))


class MetricGridBoundsTests(unittest.TestCase):
    def test_near_vertical_family_can_cross_both_slope_signs(self):
        segments = []
        for y in np.linspace(50, 420, 16):
            slope = (y + 400) / 4000
            segments.append([0, y, 486, y + 486 * slope])
        for x in np.linspace(10, 475, 16):
            top_x = 350 + (x - 350) * 400 / 850
            segments.append([top_x, 0, x, 450])
        segments.extend([[0, 100, 100, 200], [100, 100, 200, 200], [200, 100, 300, 200]])
        with patch('dev_tools.minimap_reconstruct.cv2.HoughLinesP',
                   return_value=np.round(segments).astype(np.int32)[:, None]):
            horizon, report = grid_horizon_directions(np.zeros((462, 486, 3), np.uint8))
        self.assertAlmostEqual(horizon, -400, delta=10)
        self.assertLess(max(report['residuals']), 3)

    def test_one_grid_direction_is_not_a_calibration(self):
        segments = np.array([[[0, y, 400, y + 40]] for y in range(0, 300, 20)])
        with patch('dev_tools.minimap_reconstruct.cv2.HoughLinesP', return_value=segments):
            with self.assertRaisesRegex(ValueError, 'Only one grid direction'):
                grid_horizon_directions(np.zeros((462, 486, 3), np.uint8))

    def test_degenerate_projection_is_rejected_before_allocating_canvas(self):
        for corners in [np.array([[0, 0], [100000, 100000]]),
                        np.array([[0, 0], [5000, 5000]]),
                        np.array([[0, 0], [np.inf, 10]])]:
            with self.subTest(corners=corners):
                with self.assertRaisesRegex(ValueError, 'calibration canvas limit'):
                    MetricGrid._layout(np.eye(3), corners, 1)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tracker = FlowTracker(SimpleNamespace(matrix=np.eye(3)))
        self.image = np.full((462, 486, 3), (65, 40, 20), np.uint8)
        rng = np.random.default_rng(21)
        for x, y in rng.integers((15, 15), (465, 440), (450, 2)):
            cv2.rectangle(self.image, (x, y), (x + 3, y + 3), (115, 80, 30), -1)

    def test_measures_translation_in_both_directions(self):
        for dx, dy in [(9, -6), (-8, 5)]:
            after = cv2.warpAffine(self.image, np.float32([[1, 0, dx], [0, 1, dy]]), (486, 462))
            result = self.tracker.measure(self.image, after)
            self.assertIsNotNone(result)
            np.testing.assert_allclose(result['delta'], (dx, dy), atol=0.4)

    def test_click_flash_does_not_move_stationary_grid(self):
        after = self.image.copy()
        diamond = np.array([[300, 80], [350, 130], [300, 180], [250, 130]])
        cv2.fillPoly(after, [diamond], (255, 170, 40))
        cv2.circle(after, (300, 130), 20, (255, 255, 255), -1)
        result = self.tracker.measure(self.image, after)
        self.assertIsNotNone(result)
        np.testing.assert_allclose(result['delta'], (0, 0), atol=0.1)

    def test_featureless_image_has_no_motion_evidence(self):
        image = np.zeros_like(self.image)
        self.assertIsNone(self.tracker.measure(image, image))

    def test_tracks_grid_independently_of_stationary_background_texture(self):
        rng = np.random.default_rng(45)
        texture = rng.integers(30, 90, self.image.shape[:2], dtype=np.uint8)
        background = np.stack([texture, texture // 2, texture // 3], axis=2)
        grid = np.zeros(self.image.shape[:2], np.uint8)
        grid[20::40, :] = 255
        grid[:, 20::40] = 255
        before = background.copy()
        before[grid > 0] = (120, 85, 30)
        moved = cv2.warpAffine(grid, np.float32([[1, 0, 7], [0, 1, -5]]), (486, 462))
        after = background.copy()
        after[moved > 0] = (120, 85, 30)
        result = self.tracker.measure(before, after)
        self.assertIsNotNone(result)
        np.testing.assert_allclose(result['delta'], (7, -5), atol=0.4)


class JointRegistrationTests(unittest.TestCase):
    @staticmethod
    def scene(grid_shift=(0, 0), road_shift=(0, 0)):
        grid = np.full((500, 600, 3), (45, 30, 15), np.uint8)
        for x in range(24, 600, 48):
            cv2.line(grid, (x, 0), (x, 499), (120, 85, 30), 2)
        for y in range(24, 500, 48):
            cv2.line(grid, (0, y), (599, y), (120, 85, 30), 2)
        grid = cv2.warpAffine(grid, np.float32([[1, 0, grid_shift[0]], [0, 1, grid_shift[1]]]),
                              (600, 500), borderValue=(45, 30, 15))
        roads = np.zeros(grid.shape[:2], np.uint8)
        cv2.rectangle(roads, (140, 120), (210, 360), 255, -1)
        cv2.rectangle(roads, (170, 280), (370, 360), 255, -1)
        cv2.rectangle(roads, (300, 190), (360, 310), 255, -1)
        roads = cv2.warpAffine(roads, np.float32([[1, 0, road_shift[0]], [0, 1, road_shift[1]]]), (600, 500))
        grid[roads > 0] = (210, 140, 40)
        return grid

    def test_perspective_grid_and_road_layers_can_have_different_displacements(self):
        matrix = np.array([[1.1, 0.08, -25], [0.06, 1.2, -10], [0.00015, 0.0005, 1]])
        matcher = JointRegistration(matrix, (600, 500), np.full((462, 486), 255, np.uint8), 48)
        images = [cv2.warpPerspective(im, np.linalg.inv(matrix), (486, 462)) for im in
                  (self.scene(), self.scene((36, -24), (48, -32)))]
        features = [matcher.features(im) for im in images]
        result = matcher.measure(*features, np.array([44, -28]), np.eye(2) * 4 / 3, 96)
        self.assertIsNotNone(result)
        np.testing.assert_allclose(result['delta'], [48, -32], atol=3)
        self.assertGreater(result['road_iou'], 0.9)
        self.assertGreater(result['gap'], 0.04)

    def test_grid_alone_does_not_invent_a_road_constraint(self):
        matcher = JointRegistration(np.eye(3), (240, 240), np.full((240, 240), 255, np.uint8), 48)
        empty = np.zeros(matcher.valid.shape, np.uint8)
        grid = np.zeros_like(empty)
        grid[::12, :] = 255
        features = (empty, grid, np.full_like(grid, 255))
        self.assertIsNone(matcher.measure(features, features, np.zeros(2), np.eye(2), 96))
        self.assertIsNone(matcher.measure_roads(features, features, np.zeros(2), 96))

    def test_distinct_roads_align_despite_background_parallax(self):
        matcher = JointRegistration(np.eye(3), (600, 500), np.full((500, 600), 255, np.uint8), 48)
        first = matcher.features(self.scene())
        second = matcher.features(self.scene((10, -50), (48, -32)))
        result = matcher.measure_roads(first, second, np.array([48, -60]), 96)
        self.assertIsNotNone(result)
        np.testing.assert_allclose(result['delta'], [48, -32], atol=3)

    def test_straight_road_does_not_invent_position_along_its_length(self):
        matcher = JointRegistration(np.eye(3), (400, 400), np.full((400, 400), 255, np.uint8), 48)
        image = np.full((400, 400, 3), (45, 30, 15), np.uint8)
        image[170:230, :] = (210, 140, 40)
        features = matcher.features(image)
        self.assertIsNone(matcher.measure_roads(features, features, np.zeros(2), 96))

    def test_motion_calibration_rejects_outliers(self):
        rng = np.random.default_rng(42)
        moves = rng.uniform(-80, 80, (40, 2))
        expected = np.array([[1.3, 0.01], [-0.02, 1.35]])
        roads = moves @ expected
        roads[:4] += [80, -70]
        result = JointRegistration.fit_motion(moves, roads, 48)
        self.assertIsNotNone(result)
        np.testing.assert_allclose(result[0], expected, atol=0.015)
        self.assertEqual(result[1]['inliers'], 36)

    def test_one_direction_cannot_calibrate_two_axes(self):
        moves = np.column_stack([np.arange(30), np.zeros(30)])
        self.assertIsNone(JointRegistration.fit_motion(moves, moves * 1.3, 48))

    def test_revisit_constraints_correct_accumulated_drift(self):
        truth = np.array([[0, 0], [80, 0], [80, 80], [0, 80], [0, 0]], float)
        recorded = truth / 1.3 + np.arange(5)[:, None] * [2, -3]
        edges = [{'a': i, 'b': i + 1, 'delta': (truth[i] - truth[i + 1]).tolist()}
                 for i in range(4)]
        edges.append({'a': 0, 'b': 4, 'delta': [0, 0]})
        corrected, residual = solve_joint_positions(recorded, [], edges, np.eye(2) * 1.3, 48)
        np.testing.assert_allclose(corrected, truth, atol=0.1)
        self.assertLess(float(residual.max()), 0.1)


class ProjectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name)
        self.matrix = np.array([[1.1, 0.08, -25], [0.06, 1.2, -10], [0.00015, 0.0005, 1]])
        self.poses = np.array([[0, 0], [48, 0], [96, 0], [96, 48], [48, 48], [0, 48], [0, 96],
                               [48, 96], [96, 96], [96, 48], [48, 48], [0, 48], [0, 0], [48, 0],
                               [96, 0], [96, 48], [48, 48], [0, 48], [0, 0]], float)
        self.source = np.full((462, 486), 255, np.uint8)
        self.data = dict(matrix=self.matrix.tolist(), warp_size=[600, 500], cell_px=48., strokes=[{}],
                         frames=[dict(file=f'{i}.png', position=p.tolist()) for i, p in enumerate(self.poses)],
                         moves=[dict(a=i, b=i + 1, delta=(self.poses[i] - self.poses[i + 1]).tolist())
                                for i in range(len(self.poses) - 1)])
        for i, position in enumerate(self.poses):
            scene = JointRegistrationTests.scene(tuple(-position), tuple(-position * 1.3))
            image = cv2.warpPerspective(scene, np.linalg.inv(self.matrix), (486, 462))
            cv2.imwrite(str(self.output / f'{i}.png'), image)
        wrong = np.array([[1., 0, 0], [0, 1., 0], [0, 0.0015, 1.]]) @ self.matrix
        self.bad, _ = reproject_scan(self.data, wrong, (600, 500), self.source.shape)
        calibration = SimpleNamespace(matrix=self.matrix, size=(600, 500))
        patcher = patch('dev_tools.minimap_reconstruct.MetricGrid', return_value=calibration)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_wrong_perspective_recovers_known_road_poses_without_mutating_scan(self):
        original = json.dumps(self.bad)
        result, report = recover_map_projection(self.output, self.bad, self.source)
        self.assertIsNotNone(result)
        self.assertEqual(report['status'], 'accepted')
        self.assertEqual(result[2]['status'], 'joint_grid_road')
        # Ground truth includes road/grid parallax and returns to the starting location.
        np.testing.assert_allclose(result[1], self.poses * 1.3, atol=2)
        self.assertEqual(json.dumps(self.bad), original)

    def test_good_projection_is_not_replaced_without_overlap_improvement(self):
        result, report = recover_map_projection(self.output, self.data, self.source)
        self.assertIsNone(result)
        self.assertEqual(report['reason'], 'no_better_projection')

    def test_rebuild_checks_perspective_even_after_nominal_registration_success(self):
        """低约束残差不能跳过透视复核；成功路径也应修复已知的投影畸变。"""
        import dev_tools.minimap_reconstruct as reconstruct

        data = json.loads(json.dumps(self.bad))
        data.update(roi=[0, 0, 486, 462], status='roads_exhausted', coverage_definition='test',
                    display_orientation='screen_oblique')
        for i, frame in enumerate(data['frames']):
            frame.update(id=i, kind='node')
        original = json.dumps(data)
        refine = reconstruct.refine_map_positions
        calls = []

        def initially_accepted(output, scan, source):
            calls.append(scan)
            if len(calls) == 1:
                return np.asarray([f['position'] for f in scan['frames']]), {
                    'status': 'joint_grid_road', 'residual_median_px': 0.001}
            return refine(output, scan, source)

        with patch.object(reconstruct, 'refine_map_positions', side_effect=initially_accepted):
            rebuild_v2(self.output, data)
        report = json.loads((self.output / 'registration.json').read_text())
        self.assertEqual(report['projection_recovery']['status'], 'accepted')
        self.assertEqual(report['original_registration']['status'], 'joint_grid_road')
        np.testing.assert_allclose(report['positions'], self.poses * 1.3, atol=2)
        self.assertEqual(json.dumps(data), original)

    def test_better_overlap_alone_does_not_bypass_joint_registration(self):
        self.bad['strokes'] = []
        result, report = recover_map_projection(self.output, self.bad, self.source)
        self.assertIsNone(result)
        self.assertEqual(report['reason'], 'recalibrated_registration_failed')


class ClampTests(unittest.TestCase):
    @staticmethod
    def sample(delta=(0, 0), cursor=(15, 0), spread=0.1):
        return dict(delta=list(delta), cursor_delta=list(cursor), spread=spread)

    def test_stationary_tail_requires_physical_cursor_travel(self):
        self.assertFalse(stationary_tail([self.sample(cursor=(0, 0)) for _ in range(10)]))
        self.assertFalse(stationary_tail([self.sample() for _ in range(3)]))
        self.assertTrue(stationary_tail([self.sample() for _ in range(5)]))

    def test_reverse_motion_is_not_a_clamp(self):
        self.assertFalse(stationary_tail([self.sample(delta=(-15, 0)) for _ in range(6)]))

    def test_ending_motion_resets_stationary_evidence(self):
        samples = [self.sample() for _ in range(6)]
        samples.append(self.sample(delta=(5, 0)))
        self.assertFalse(stationary_tail(samples))

    def test_subpixel_shimmer_does_not_hide_stationary_gesture(self):
        samples = [self.sample(delta=(0.5, -0.3)) for _ in range(15)]
        self.assertTrue(stationary_stroke(samples))
        samples[-1] = self.sample(delta=(7, 0), cursor=(0, 0))
        self.assertFalse(stationary_stroke(samples))

class RoadFrontierTests(unittest.TestCase):
    def setUp(self):
        self.scanner = DriftScanner.__new__(DriftScanner)
        self.scanner.grid = SimpleNamespace(cell=48.0, valid=np.full((200, 200), 255, np.uint8))
        self.scanner.current_terrain = np.zeros((200, 200), np.uint8)
        self.scanner.position = np.zeros(2)
        self.scanner.args = SimpleNamespace(grid_step=240.0)
        self.scanner.frontiers = []
        self.scanner.visited = []
        self.scanner.camera_limits = {}

    def test_road_ending_inside_view_does_not_scan_empty_grid(self):
        self.scanner.current_terrain[70:130, 60:140] = 255
        self.scanner.queue_frontiers()
        self.assertEqual(self.scanner.frontiers, [])

    def test_only_road_crossing_view_edge_creates_frontier(self):
        self.scanner.current_terrain[70:130, :140] = 255
        self.scanner.queue_frontiers()
        self.assertEqual(len(self.scanner.frontiers), 1)
        np.testing.assert_allclose(self.scanner.frontiers[0], [-240, 0])
        self.scanner.visited.append(self.scanner.frontiers.pop())
        self.scanner.queue_frontiers()
        self.assertEqual(self.scanner.frontiers, [])

    def test_few_bright_edge_pixels_do_not_create_branch(self):
        self.scanner.current_terrain[90:93, :3] = 255
        self.scanner.queue_frontiers()
        self.assertEqual(self.scanner.frontiers, [])

    def test_camera_limit_preserves_reachable_edge_branches(self):
        self.scanner.camera_limits = {(0, 1): 100.0}
        target = self.scanner.clip_target(np.array([240.0, 80.0]))
        np.testing.assert_allclose(target, [100, 80])


if __name__ == '__main__':
    unittest.main()
