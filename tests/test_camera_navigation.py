import copy
import hashlib
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.campaign_prototype import camera_navigation as camera
from module.campaign_prototype.manual_move import GameSession


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.field = np.zeros((999, 1776, 3), np.uint8)
        self.observation = dict(position=[243, 231], position_kind='squad', player_roi=[243, 231],
                                roi_to_map=np.eye(3).tolist(), iou=.95)
        self.session = SimpleNamespace(localizer=SimpleNamespace(old_matrix=np.eye(3)),
                                       check=Mock(), identity=Mock(), emit=Mock(), check_collectible=Mock(),
                                       preview=Mock(), win=Mock(), pause=Mock(), folder=Path('unused'), index=0)

    def planning(self):
        stack = ExitStack()
        stack.enter_context(patch('module.campaign_prototype.goto.map_close'))
        stack.enter_context(patch('module.campaign_prototype.goto.capture_client', return_value=self.field))
        stack.enter_context(patch('module.campaign_prototype.movement_feedback.resolve_anchor',
                                  return_value=np.array([888, 500])))
        return stack

    def view(self, target):
        matrix = np.eye(3)
        matrix[:2, 2] = np.asarray(target) - [243, 231]
        return dict(position=list(target), position_kind='viewport_center', player_roi=None,
                    roi_to_map=matrix.tolist(), iou=.95)

    def test_visible_target_beyond_old_step_is_clicked_without_shortening(self):
        target = np.array([360, 240])
        with self.planning(), patch.object(camera, 'pan_scene') as pan:
            click = camera.plan_world_move(self.session, self.observation, target)
        np.testing.assert_allclose(click, [888, 500] + camera.goto.A_INV @ (target - [243, 231]))
        self.assertGreater(np.linalg.norm(click - [888, 500]), 240)
        pan.assert_not_called()
        self.assertEqual(self.session.emit.call_args.kwargs['navigation_method'], 'direct_target')

    def test_large_pan_uses_field_width_and_observed_response(self):
        chart = camera.field_chart(self.session.localizer, self.observation, [888, 500])
        horizontal = np.linalg.solve(camera.goto.A_INV, [2500, 0]) + [243, 231]
        drag = camera.camera_drag(chart, self.observation, horizontal)
        np.testing.assert_allclose(drag, [-1728, 0], atol=1e-10)
        self.session.win.gui.ClientToScreen.return_value = (0, 0)
        self.session.win.handler._failures = 0
        with self.planning():
            camera.pan_scene(self.session, drag)
        start, end = self.session.win.handler.mouse_swipe.call_args.args
        self.assertEqual(start, (1752, 600))
        self.assertEqual(end, (24, 600))
        next_center = [243, 231] + np.linalg.solve(camera.goto.A_INV, -drag * .5)
        gain = camera.camera_response(chart, self.observation, self.view(next_center), drag)
        self.assertAlmostEqual(gain, .5)
        near = np.linalg.solve(camera.goto.A_INV, [400, 0]) + [243, 231]
        np.testing.assert_allclose(camera.camera_drag(chart, self.observation, near, gain=gain),
                                   [-800, 0], atol=1e-10)
        self.assertIsNone(camera.camera_response(chart, self.observation, self.observation, drag))

    def test_far_target_pans_and_keeps_original_squad_position(self):
        target = [700, 500]
        self.session.camera_pans = 2
        with self.planning(), patch.object(camera, 'pan_scene') as pan, patch.object(
                camera, 'observe_camera', return_value=self.view(target)) as observe:
            click = camera.plan_world_move(self.session, self.observation, target)
        np.testing.assert_allclose(click, [888, 500])
        pan.assert_called_once()
        np.testing.assert_array_equal(observe.call_args.args[1], [243, 231])
        self.assertEqual(self.observation['position'], [243, 231])
        self.assertEqual(self.session.emit.call_args.kwargs['navigation_method'], 'camera_pan')
        self.assertEqual(self.session.emit.call_args.kwargs['camera_pans'], 3)
        self.assertEqual(self.session.emit.call_args.kwargs['plan_camera_pans'], 1)

    def test_full_height_and_diagonal_drags_stay_in_clear_scene_regions(self):
        chart = camera.field_chart(self.session.localizer, self.observation, [888, 500])
        self.session.win.gui.ClientToScreen.return_value = (100, 200)
        self.session.win.handler._failures = 0
        for direction in ([0, 2500], [0, -2500], [2500, 2500], [-2500, -2500],
                          [2500, -2500], [-2500, 2500]):
            with self.subTest(direction=direction):
                target = [243, 231] + np.linalg.solve(camera.goto.A_INV, direction)
                drag = camera.camera_drag(chart, self.observation, target)
                self.assertAlmostEqual(abs(drag[1]), 810)
                with self.planning():
                    camera.pan_scene(self.session, drag)
                start, end = self.session.win.handler.mouse_swipe.call_args.args
                start, end = np.asarray(start) - [100, 200], np.asarray(end) - [100, 200]
                self.assertTrue(camera.inside(start, (250, 70, 1490, 880)))
                self.assertTrue(camera.inside(end, (250, 70, 1490, 880)))
                np.testing.assert_allclose(end - start, drag, atol=1)

    def test_planner_compensates_for_partial_camera_response(self):
        target = np.array([243, 231]) + np.linalg.solve(camera.goto.A_INV, [2100, 0])
        first_center = np.array([243, 231]) + np.linalg.solve(camera.goto.A_INV, [864, 0])
        with self.planning(), patch.object(camera, 'pan_scene') as pan, patch.object(
                camera, 'observe_camera', side_effect=[self.view(first_center), self.view(target)]):
            camera.plan_world_move(self.session, self.observation, target)
        self.assertEqual(pan.call_count, 2)
        np.testing.assert_allclose(pan.call_args_list[1].args[1], [-1728, 0], atol=1e-9)
        gains = [call.kwargs['camera_gain'] for call in self.session.emit.call_args_list
                 if call.kwargs.get('state') == 'panning']
        np.testing.assert_allclose(gains, [1, .5])

    def test_pan_failure_boundary_and_budget_never_produce_click(self):
        for failure in (RuntimeError('focus lost'), KeyboardInterrupt('stop')):
            with self.planning(), patch.object(
                    camera, 'pan_scene', side_effect=failure), self.assertRaises(type(failure)):
                camera.plan_world_move(self.session, self.observation, [1000, 1000])
        with self.planning(), patch.object(camera, 'pan_scene'), patch.object(
                camera, 'observe_camera', return_value=self.observation):
            with self.assertRaisesRegex(ValueError, '边界'):
                camera.plan_world_move(self.session, self.observation, [1000, 1000])
        with self.planning(), patch.object(camera, 'pan_scene'), patch.object(
                camera, 'observe_camera', side_effect=[self.view([300, 300]), self.view([400, 400])]):
            with self.assertRaisesRegex(ValueError, '已平移 2 次'):
                camera.plan_world_move(self.session, self.observation, [1000, 1000], max_pans=2)

    def test_chart_preserves_perspective_and_local_layer_calibration(self):
        old = np.array([[1.2, .1, 4], [.1, .8, -2], [.001, .002, 1]])
        self.session.localizer.old_matrix = old
        chart = camera.field_chart(self.session.localizer, self.observation, [888, 500])
        np.testing.assert_allclose(camera.project(chart, [243, 231]), [888, 500])
        target = [340, 270]
        expected = [888, 500] + camera.goto.A_INV @ (
            camera.project(old, target) - camera.project(old, [243, 231]))
        np.testing.assert_allclose(camera.project(chart, target), expected)
        local = {'matrix': [[4, 0, 2], [0, 3, -1], [0, 0, 1]]}
        chart = camera.field_chart(self.session.localizer, self.observation, [888, 500], local)
        np.testing.assert_allclose(camera.project(chart, [253, 251]), [928, 560])
        with self.assertRaisesRegex(ValueError, '真实小队'):
            camera.field_chart(self.session.localizer, self.view([243, 231]), [888, 500])

    def test_observing_offscreen_squad_does_not_replace_it_with_view_center(self):
        self.session.localizer.locate = Mock(return_value=self.view([700, 500]))
        self.session.win.capture.return_value = np.zeros((462, 486, 3), np.uint8)
        with self.planning(), patch('module.campaign_prototype.goto.map_open'), patch(
                'module.campaign_prototype.runtime.write_image'):
            view = camera.observe_camera(self.session, [243, 231])
        self.assertEqual(view['position_kind'], 'viewport_center')
        self.assertIsNone(view['player_roi'])
        self.session.localizer.locate.assert_called_once()
        self.assertIs(self.session.localizer.locate.call_args.kwargs['require_player'], False)
        self.session.localizer.locate.return_value = {**self.observation, 'position': [280, 231]}
        with self.planning(), patch('module.campaign_prototype.goto.map_open'):
            with self.assertRaisesRegex(ValueError, '小队位置发生变化'):
                camera.observe_camera(self.session, [243, 231])

    def test_static_camera_does_not_end_wait_while_squad_moves_or_is_hidden(self):
        players = [[], [[100, 100]], [[115, 100]], [[130, 100]], [],
                   [[150, 100]], [[150, 100]], [[150, 100]]]
        self.session.win.capture.return_value = np.zeros((462, 486, 3), np.uint8)
        with self.planning(), patch('module.campaign_prototype.goto.map_open') as open_map, patch(
                'module.campaign_prototype.goto.mr.detect_markers', side_effect=[(p, []) for p in players]):
            camera.wait_for_squad(self.session)
        self.assertEqual(self.session.pause.call_count, 32)
        self.assertEqual(open_map.call_count, 8)
        self.session.preview.assert_called_once()

    def test_stationary_marker_pulse_and_missing_marker_timeout(self):
        pulse = [[143.5, 193.5], [147.5, 199.5], [144.5, 192.5]]
        self.session.win.capture.return_value = np.zeros((462, 486, 3), np.uint8)
        panel = {'expanded': False}
        snapshots = [([], [])] + [([p], []) for p in pulse]
        self.session.pause.side_effect = lambda seconds: self.assertFalse(panel['expanded'])
        with self.planning(), patch('module.campaign_prototype.goto.map_close',
                                   side_effect=lambda win: panel.update(expanded=False)), patch(
                'module.campaign_prototype.goto.map_open',
                side_effect=lambda win: panel.update(expanded=True)) as open_map, patch(
                'module.campaign_prototype.goto.mr.detect_markers', side_effect=snapshots):
            camera.wait_for_squad(self.session)
        self.assertEqual(self.session.pause.call_count, 16)
        self.assertEqual(open_map.call_count, 4)
        self.assertFalse(panel['expanded'])
        self.session.preview.reset_mock()
        with self.planning(), patch('module.campaign_prototype.goto.map_open'), patch(
                'module.campaign_prototype.goto.mr.detect_markers', return_value=([], [])):
            with self.assertRaisesRegex(RuntimeError, '超时'):
                camera.wait_for_squad(self.session)
        self.session.preview.assert_not_called()

    def test_parallax_camera_rejects_reference_outside_calibrated_surface(self):
        self.session.calibration = ({}, None)
        self.session.localizer.locate = Mock(return_value={**self.view([300, 200]), 'status': 'accepted'})
        self.session.win.capture.return_value = np.zeros((462, 486, 3), np.uint8)
        surface = np.zeros((700, 700), np.uint8)
        surface[100:400, 100:400] = 1
        with self.planning(), patch('module.campaign_prototype.goto.map_open'), patch(
                'module.campaign_prototype.goto.mr.detect_markers', return_value=([], [])), patch(
                'module.campaign_prototype.goto.mr.terrain', return_value=np.ones((462, 486), np.uint8)), patch(
                'module.campaign_prototype.runtime.write_image'):
            report = camera.observe_camera(self.session, [243, 231], surface)
            self.assertEqual(report['position_kind'], 'viewport_reference')
            self.session.localizer.locate.return_value['position'] = [500, 200]
            with self.assertRaisesRegex(ValueError, '同层道路'):
                camera.observe_camera(self.session, [243, 231], surface)

    def test_parallax_plan_uses_shared_planner_with_its_own_supported_surface(self):
        from module.campaign_prototype.parallax_movement import ParallaxSessionMixin
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'movement_calibration/normal/surface.png'
            path.parent.mkdir(parents=True)
            surface = np.ones((700, 700), np.uint8) * 255
            cv2.imwrite(str(path), surface)
            data = dict(origin=[243, 231], radius=250, support=[[-200, -200], [200, -200], [200, 200], [-200, 200]],
                        matrix=np.eye(3).tolist(), surface_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.session.calibration = (data, None)
            self.session.request = dict(package=directory, difficulty='normal')
            self.session.localizer.road = surface > 0
            with patch.object(camera, 'plan_world_move', return_value=np.array([888, 500])) as plan:
                ParallaxSessionMixin.plan(self.session, self.observation, [360, 240])
                np.testing.assert_array_equal(plan.call_args.args[2], [360, 240])
                self.assertIs(plan.call_args.kwargs['calibration'], data)
                self.assertTrue(plan.call_args.kwargs['surface'].all())
                plan.reset_mock()
                with self.assertRaisesRegex(ValueError, '实测标定范围'):
                    ParallaxSessionMixin.plan(self.session, self.observation, [600, 240])
                self.session.localizer.road[230:250, 300:310] = False
                with self.assertRaisesRegex(ValueError, '同层连续道路'):
                    ParallaxSessionMixin.plan(self.session, self.observation, [360, 240])
                plan.assert_not_called()

    def test_all_management_purposes_use_the_full_target_planner(self):
        target = np.array([700, 500])
        self.session.localizer.route = Mock(return_value=(np.array([270, 250]), np.array([[243, 231], target])))
        for purpose in ('position', 'collectible', 'enemy'):
            self.session.request = {'purpose': purpose}
            with self.subTest(purpose=purpose), patch.object(camera, 'plan_world_move', return_value=target) as plan:
                GameSession.plan(self.session, self.observation, target)
                np.testing.assert_array_equal(plan.call_args.args[2], target)

    def test_standalone_live_probe_and_run_dispatch_to_shared_planner(self):
        from module.campaign_prototype import live, probe, run
        with tempfile.TemporaryDirectory() as directory:
            for module in (live, probe, run):
                report = {**self.observation, 'position': [243, 231]}
                localizer = Mock(targets={13: np.array([300, 250])}, old_matrix=np.eye(3))
                localizer.locate.side_effect = lambda *args, **kwargs: copy.deepcopy(report)
                localizer.route.return_value = (np.array([260, 240]), np.array([[243, 231], [300, 250]]))
                localizer.package.binding = {}
                window = Mock(hwnd=1)
                window.handler._failures = 0
                window.gui.GetForegroundWindow.return_value = 1
                window.gui.ClientToScreen.return_value = (0, 0)
                window.capture.return_value = np.zeros((462, 486, 3), np.uint8)
                argv = ['test', '--tag', module.__name__.split('.')[-1], '--target', '13']
                argv += ['--steps', '1'] if module is probe else ['--move']
                with self.subTest(module=module.__name__), ExitStack() as stack:
                    for name, value in [('sys.argv', argv),
                                        ('module.campaign_prototype.settings.output', Path(directory))]:
                        stack.enter_context(patch(name, value))
                    for name in ('settings.configure', 'runtime.finish', 'runtime.write_image',
                                 'goto.map_open', 'goto.map_close', 'camera_navigation.wait_for_squad'):
                        stack.enter_context(patch(f'module.campaign_prototype.{name}'))
                    stack.enter_context(patch('module.campaign_prototype.runtime.Window', return_value=window))
                    stack.enter_context(patch('module.campaign_prototype.goto.capture_client', return_value=self.field))
                    stack.enter_context(patch('module.campaign_prototype.goto.battle_popup_score', return_value=0))
                    for name in ('live.AdaptiveLocalizer', 'adaptive.AdaptiveLocalizer', 'probe.Localizer'):
                        stack.enter_context(patch(f'module.campaign_prototype.{name}', return_value=localizer))
                    stack.enter_context(patch('module.campaign_prototype.arrow_anchor.sample_window_anchor',
                                              return_value=(np.array([888, 500]), self.field.copy())))
                    stack.enter_context(patch('module.campaign_prototype.run.sample_window_anchor',
                                              return_value=(np.array([888, 500]), self.field.copy())))
                    plan = stack.enter_context(patch.object(
                        camera, 'plan_world_move', return_value=np.array([800, 500])))
                    module.main()
                    plan.assert_called_once()
                    window.handler.mouse_click.assert_called_once_with(800, 500)


if __name__ == '__main__':
    unittest.main()
