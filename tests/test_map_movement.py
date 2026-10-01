"""移动任务的版本绑定、单任务互斥、取消及导航闭环；不取得真实设备控制。"""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from dev_tools.map_movement import MovementJobs
from module.campaign_prototype.manual_move import navigate, movement_plan, prepare
from module.campaign_prototype import runtime


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Mock()
        self.loaded = {'id': 'chapter_38', 'chapter': 38, 'path': str(self.root / 'source'),
                       'revision': 'rev', 'size': [200, 150], 'annotations': {'image_sha256': 'hash'}}
        Path(self.loaded['path']).mkdir()
        (Path(self.loaded['path']) / 'map.json').write_text('{}')
        self.store.load.return_value = self.loaded
        self.jobs = MovementJobs(self.store, self.root / 'runs')
        self.payload = {'id': 'chapter_38', 'revision': 'rev', 'image_sha256': 'hash',
                        'target': [80.25, 60.5], 'difficulty': 'normal'}

    def test_rejects_stale_or_invalid_target_before_starting_process(self):
        for fields in [{'revision': 'old'}, {'image_sha256': 'old'}, {'target': [True, 1]},
                       {'target': [float('nan'), 1]}, {'target': [-1, 50]}, {'target': [200, 50]},
                       {'target': [1]}, {'difficulty': 'all'}, {'auto_calibrate': 'true'}]:
            with self.subTest(fields=fields), patch('dev_tools.map_movement.subprocess.Popen') as start:
                with self.assertRaises(ValueError):
                    self.jobs.start(dict(self.payload, **fields))
                start.assert_not_called()

    def test_freezes_coordinates_serializes_start_and_stops_only_current_job(self):
        process = Mock()
        process.poll.return_value = None
        with patch('dev_tools.map_movement.subprocess.Popen', return_value=process) as start:
            first = self.jobs.start(dict(self.payload, auto_calibrate=True))
            folder = self.jobs.output / first['id']
            saved = json.loads((folder / 'request.json').read_text())
            self.assertEqual(saved['target'], [80.25, 60.5])
            self.assertIs(saved['auto_calibrate'], False)
            self.assertIn('module.campaign_prototype.manual_move', start.call_args.args[0])
            with self.assertRaisesRegex(ValueError, '已有'):
                self.jobs.start(self.payload)
            with self.assertRaises(ValueError):
                self.jobs.stop('different-job')
            self.jobs.stop(first['id'])
            self.assertTrue((folder / 'STOP').exists())
            with self.assertRaises(ValueError):
                self.jobs.preview('../outside')
            process.poll.return_value = 0
            self.assertEqual(self.jobs.status()['state'], 'failed')

    def test_terminal_state_and_shutdown_cleanup(self):
        process = Mock()
        process.poll.return_value = None
        with patch('dev_tools.map_movement.subprocess.Popen', return_value=process):
            job = self.jobs.start(self.payload)
        folder = self.jobs.output / job['id']
        (folder / 'status.json').write_text(json.dumps({'state': 'arrived', 'distance': 5}))
        self.assertEqual(self.jobs.status()['state'], 'arrived')
        self.jobs.close()
        self.assertTrue((folder / 'STOP').exists())
        process.wait.assert_called_once_with(timeout=5)

    def test_unsupported_nonplanar_fails_before_package_or_device_setup(self):
        (self.root / 'map.json').write_text('{"chapter":40,"coordinate_model":"orthographic_surfaces"}')
        with patch('module.campaign_prototype.manual_move.sha', side_effect=['hash', 'rev']), patch(
                'module.campaign_prototype.manual_move.MapPackage') as package:
            with self.assertRaisesRegex(ValueError, '非平面.*标定'):
                prepare({'package': str(self.root), 'image_sha256': 'hash', 'revision': 'rev',
                         'chapter': 40}, self.root)
            package.assert_not_called()

    def test_planar_chapters_share_calibration(self):
        from module.campaign_prototype.manual_move import sha
        source = Path(self.loaded['path'])
        (source / 'source').mkdir()
        for name in ('map.png', 'annotations.json', 'source/map_data.npz'):
            (source / name).write_bytes(b'fixture')
        for chapter in (1, 33, 38, 40):
            (source / 'map.json').write_text(json.dumps(dict(chapter=chapter, size=[200, 150],
                capture=dict(whole_camera_domain_verified=False))))
            request = dict(package=str(source), image_sha256=sha(source / 'map.png'),
                           revision=sha(source / 'annotations.json'), chapter=chapter,
                           difficulty='hard', target=[80, 60])
            with patch('module.campaign_prototype.manual_move.MapPackage',
                       return_value=SimpleNamespace(terrain=np.ones((150, 200)))):
                destination, _ = prepare(request, self.root / f'run{chapter}')
            self.assertEqual((destination / 'calibration.json').read_bytes(),
                             Path('module/campaign_prototype/assets/calibration.json').read_bytes())
            self.assertEqual(json.loads((destination / 'map.json').read_text())['chapter'], chapter)
            self.assertEqual(self.jobs.calibration(self.loaded['id'], 'hard')['state'], 'shared')

    def test_pending_nonplanar_cannot_launch_or_auto_calibrate(self):
        source = Path(self.loaded['path'])
        for model in ('local_parallax', 'orthographic_surfaces', 'shared_plane_reference'):
            (source / 'map.json').write_text(json.dumps(dict(coordinate_model=model)))
            with self.subTest(model=model), patch('dev_tools.map_movement.subprocess.Popen') as start:
                with self.assertRaisesRegex(ValueError, '待定'):
                    self.jobs.start(dict(self.payload, auto_calibrate=True))
                start.assert_not_called()


class NavigationTests(unittest.TestCase):
    def session(self, positions):
        session = Mock()
        session.observe.side_effect = [dict(position=p, position_kind='squad', iou=.96) for p in positions]
        session.plan.return_value = np.array([850., 600.])
        return session

    def test_moves_then_requires_three_independent_near_observations(self):
        session = self.session([[20, 30], [95, 98], [101, 99], [99, 102]])
        result = navigate(session, [100, 100], Mock())
        self.assertEqual(result['state'], 'arrived')
        self.assertEqual(result['movement_clicks'], 1)
        session.move.assert_called_once()
        session.wait_stopped.assert_called_once()
        session.finish_view.assert_called_once()

    def test_already_arrived_does_not_click(self):
        session = self.session([[99, 98]] * 3)
        result = navigate(session, [100, 100], Mock())
        self.assertEqual(result['movement_clicks'], 0)
        session.move.assert_not_called()

    def test_localization_failure_and_cancel_cannot_send_movement(self):
        for error in [RuntimeError('ambiguous'), KeyboardInterrupt('STOP')]:
            session = self.session([])
            session.observe.side_effect = error
            with self.subTest(error=error), self.assertRaises(type(error)):
                navigate(session, [100, 100], Mock())
            session.move.assert_not_called()
        session = self.session([[20, 30]])
        session.check.side_effect = [None, KeyboardInterrupt('STOP before click')]
        with self.assertRaises(KeyboardInterrupt):
            navigate(session, [100, 100], Mock())
        session.move.assert_not_called()

    def test_stationary_or_exhausted_navigation_fails_instead_of_arriving(self):
        session = self.session([[20, 30]] * 3)
        with self.assertRaisesRegex(RuntimeError, '没有明显位移'):
            navigate(session, [100, 100], Mock())
        self.assertEqual(session.move.call_count, 2)
        session = self.session([[20, 30], [50, 60]])
        with self.assertRaisesRegex(RuntimeError, '上限'):
            navigate(session, [100, 100], Mock(), max_moves=1)
        session.move.assert_called_once()

    def test_plan_uses_current_projection_and_bounds_scene_displacement(self):
        loc = SimpleNamespace(old_matrix=np.eye(3), route=lambda a, b: (b, np.array([a, b])))
        observation = {'position_kind': 'squad', 'position': [20, 30], 'player_roi': [150, 160],
                       'roi_to_map': [[2, 0, 0], [0, 2, 0], [0, 0, 1]]}
        click, _ = movement_plan(loc, observation, np.array([120, 130]), [888, 499])
        self.assertLessEqual(np.linalg.norm(click - [888, 499]), 240)
        shifted = dict(observation, roi_to_map=np.eye(3).tolist())
        twice, _ = movement_plan(loc, shifted, np.array([120, 130]), [888, 499])
        np.testing.assert_allclose(twice - [888, 499], 2 * (click - [888, 499]))
        with self.assertRaisesRegex(ValueError, '小队'):
            movement_plan(loc, dict(observation, position_kind='viewport_center'), [120, 130], [888, 499])


class ActivationTests(unittest.TestCase):
    def test_only_first_verified_title_bar_activation_can_run_without_foreground(self):
        window = Mock(hwnd=10, _focused=False, _focus_activation=False)
        window.gui.GetForegroundWindow.return_value = 20
        window.gui.ClientToScreen.return_value = (100, 200)
        window.gui.GetAncestor.return_value = 10
        handler = Mock(_failures=0)
        guarded = runtime.GuardedInput(window, handler)
        with self.assertRaisesRegex(RuntimeError, 'foreground'):
            guarded.mouse_click(500, 180)
        window._focus_activation = True
        guarded.mouse_click(500, 180)
        handler.mouse_click.assert_called_once_with(500, 180)
        for focused, ancestor, point in [(False, 20, (500, 180)), (True, 10, (500, 180)),
                                         (False, 10, (500, 400))]:
            window._focused = focused
            window.gui.GetAncestor.return_value = ancestor
            with self.assertRaises(RuntimeError):
                guarded.mouse_click(*point)
        self.assertEqual(handler.mouse_click.call_count, 1)


if __name__ == '__main__':
    unittest.main()
