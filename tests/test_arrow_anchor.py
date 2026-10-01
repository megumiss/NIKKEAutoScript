import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.campaign_prototype.arrow_anchor import cycle_ground_anchor, sample_ground_anchor, sample_window_anchor
from module.campaign_prototype.live import squad_arrow
from module.campaign_prototype.manual_move import navigate
from module.campaign_prototype.movement_feedback import resolve_anchor


class ArrowTests(unittest.TestCase):
    def field(self, color, x=860, y=380):
        crop = cv2.imread(str(Path(__file__).parent / 'fixtures/squad_arrow' / f'{color}.png'))
        field = np.zeros((999, 1776, 3), np.uint8)
        field[y:y + crop.shape[0], x:x + crop.shape[1]] = crop
        return field

    def test_both_user_examples_track_without_ground_ring(self):
        for color in ('white', 'orange'):
            with self.subTest(color=color):
                anchor = squad_arrow(self.field(color))
                self.assertIsNotNone(anchor)
                np.testing.assert_allclose(squad_arrow(self.field(color, x=873, y=390)),
                                           anchor + [13, 10])
        self.assertIsNone(squad_arrow(np.zeros((999, 1776, 3), np.uint8)))

    def test_duplicate_arrow_is_ambiguous(self):
        field = self.field('white') | self.field('white', x=970)
        self.assertIsNone(squad_arrow(field))

    def test_verified_snow_small_arrow_and_hidden_arrow_frames(self):
        for chapter, expected in [(18, [888, 423.5]), (39, [888, 420]), (38, None)]:
            with self.subTest(chapter=chapter):
                field = np.zeros((999, 1776, 3), np.uint8)
                field[200:650, 670:1110] = cv2.imread(str(
                    Path(__file__).parent / f'fixtures/squad_arrow/ch{chapter}_verified.png'))
                actual = squad_arrow(field)
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    np.testing.assert_allclose(actual, expected, atol=1)

    def test_standalone_window_samples_arrow_and_stops_at_battle_popup(self):
        heights = [0, 1, 5, 11, 14, 8, 3, 0] * 2
        frames = [self.field('white', y=380 + h) for h in heights]
        window, evidence = Mock(), {}
        with patch('module.campaign_prototype.goto.capture_client', side_effect=frames[1:]), patch(
                'module.campaign_prototype.runtime.pause'), patch(
                'module.campaign_prototype.goto.battle_popup_score', return_value=0):
            anchor, current = sample_window_anchor(window, frames[0], evidence)
        np.testing.assert_allclose(anchor, [884, 497], atol=1)
        self.assertIs(current, frames[-1])
        self.assertEqual(evidence['anchor_method'], 'arrow_cycle')
        with patch('module.campaign_prototype.goto.capture_client', return_value=frames[1]), patch(
                'module.campaign_prototype.runtime.pause'), patch(
                'module.campaign_prototype.goto.battle_popup_score', side_effect=[0, 1]):
            with self.assertRaisesRegex(RuntimeError, 'Battle popup'):
                sample_window_anchor(window, frames[0], {})

    def test_cycle_phase_and_color_changes_preserve_ground_anchor(self):
        heights = [0, 1, 5, 11, 14, 8, 3, 0] * 2
        for phase in range(8):
            frames = [self.field('white' if i < 8 else 'orange', y=380 + h)
                      for i, h in enumerate(np.roll(heights, phase))]
            session = SimpleNamespace(check=Mock(), pause=Mock(), identity=Mock(), emit=Mock(), win=Mock())
            with patch('module.campaign_prototype.goto.capture_client', side_effect=frames[1:]):
                anchor, _ = sample_ground_anchor(session, frames[0])
            np.testing.assert_allclose(anchor, [884, 497], atol=1)
            self.assertEqual(session.pause.call_count, 15)

    def test_rejects_stationary_incomplete_or_moving_observations(self):
        cycle = np.array([[884, 399.5 + y] for y in [0, 1, 5, 11, 14, 8, 3, 0] * 2])
        for points in (cycle[:8], np.tile([884, 400], (16, 1)),
                       cycle + np.column_stack([np.arange(16), np.zeros(16)]),
                       cycle + np.column_stack([np.zeros(16), np.arange(16)])):
            with self.assertRaises(ValueError):
                cycle_ground_anchor(points)

    def failed_ch48_frame(self):
        field = np.zeros((999, 1776, 3), np.uint8)
        field[350:600, 760:1020] = cv2.imread(str(
            Path(__file__).parent / 'fixtures/squad_arrow/ch48_low_score.png'))
        return field

    def test_ch48_arrow_is_detected_and_intermittent_dropouts_recover(self):
        np.testing.assert_allclose(squad_arrow(self.failed_ch48_frame()), [893, 417.5], atol=1)
        missed = np.zeros((999, 1776, 3), np.uint8)
        heights = [0, 1, 5, 11, 14, 8, 3, 0] * 2
        frames = [self.field('orange', x=869, y=380 + h) for h in heights]
        frames[2] = frames[10] = missed
        frames.insert(0, missed)
        session = SimpleNamespace(check=Mock(), pause=Mock(), identity=Mock(), emit=Mock(), win=Mock())
        with patch('module.campaign_prototype.goto.capture_client', side_effect=frames[1:]):
            anchor, current = sample_ground_anchor(session, frames[0])
        np.testing.assert_allclose(anchor, [893, 497], atol=1)
        self.assertTrue(np.isfinite(anchor).all())
        self.assertIs(current, frames[15])
        evidence = session.emit.call_args.kwargs
        self.assertEqual(evidence['arrow_missed_frames'], 3)
        self.assertEqual(evidence['arrow_samples'].count(None), 3)

    def test_incomplete_initial_cycle_retries_in_a_new_window(self):
        heights = [0] * 8 + [0, 1, 5, 11, 14, 8, 3, 0] * 2
        frames = [self.field('white', y=380 + h) for h in heights]
        session = SimpleNamespace(check=Mock(), pause=Mock(), identity=Mock(), emit=Mock(), win=Mock())
        with patch('module.campaign_prototype.goto.capture_client', side_effect=frames[1:]):
            anchor, _ = sample_ground_anchor(session, frames[0])
        np.testing.assert_allclose(anchor, [884, 497], atol=1)
        self.assertGreater(session.pause.call_count, 15)

    def test_persistent_missing_or_static_arrow_stops_at_budget(self):
        for field in (self.failed_ch48_frame(), self.field('white'),
                      self.field('white') | self.field('white', x=970)):
            session = SimpleNamespace(check=Mock(), pause=Mock(), identity=Mock(), emit=Mock(), win=Mock())
            with patch('module.campaign_prototype.goto.capture_client', return_value=field):
                with self.assertRaisesRegex(ValueError, '48 帧'):
                    sample_ground_anchor(session, field)
            self.assertEqual(session.pause.call_count, 47)
            self.assertIsNone(session.emit.call_args.kwargs['anchor_method'])

    def test_missing_arrow_does_not_skip_motion_or_cancellation_checks(self):
        field = self.failed_ch48_frame()
        session = SimpleNamespace(check=Mock(), pause=Mock(), identity=Mock(), emit=Mock(), win=Mock())
        unchanged = np.zeros((158, 179), np.uint8)
        with patch('module.campaign_prototype.goto.capture_client', return_value=field), patch(
                'module.campaign_prototype.goto.mr.terrain',
                side_effect=[unchanged, unchanged, np.ones_like(unchanged)]):
            with self.assertRaisesRegex(ValueError, '小地图道路发生变化'):
                sample_ground_anchor(session, field)
        session.check.side_effect = KeyboardInterrupt('停止测试')
        with self.assertRaises(KeyboardInterrupt):
            sample_ground_anchor(session, field)
        session.emit.assert_not_called()

    def test_navigation_continues_after_second_step_arrow_dropout(self):
        heights = [0, 1, 5, 11, 14, 8, 3, 0] * 2
        first = [self.field('white', y=380 + h) for h in heights]
        second = [self.field('orange', x=869, y=380 + h) for h in heights]
        second[0] = self.failed_ch48_frame()
        starts = iter([first[0], second[0]])
        session = SimpleNamespace(check=Mock(), pause=Mock(), identity=Mock(), emit=Mock(), win=Mock(),
                                  move=Mock(), wait_stopped=Mock(), finish_view=Mock(),
                                  observe=Mock(side_effect=[dict(position=[x, 0], position_kind='squad', iou=.95)
                                                            for x in (0, 40, 80, 80)]))
        session.plan = lambda observation, target: resolve_anchor(session, next(starts), observation) + [60, 0]
        with patch('module.campaign_prototype.goto.capture_client', side_effect=first[1:] + second[1:]):
            result = navigate(session, [80, 0], session.emit, required_near=2)
        self.assertEqual(result['state'], 'arrived')
        self.assertEqual(session.move.call_count, 2)

    def test_arrow_success_does_not_require_ring_and_retains_reference(self):
        session = SimpleNamespace()
        field = self.field('orange')
        with patch('module.campaign_prototype.arrow_anchor.sample_ground_anchor',
                   return_value=(np.array([884, 496]), field)):
            np.testing.assert_array_equal(resolve_anchor(session, field, {'position': [10, 20]}), [884, 496])
        np.testing.assert_array_equal(session.anchor_reference[2], [10, 20])


if __name__ == '__main__':
    unittest.main()
