import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.campaign_prototype.arrow_anchor import cycle_ground_anchor, sample_ground_anchor
from module.campaign_prototype.live import squad_arrow
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

    def test_arrow_success_does_not_require_ring_and_retains_reference(self):
        session = SimpleNamespace()
        field = self.field('orange')
        with patch('module.campaign_prototype.arrow_anchor.sample_ground_anchor',
                   return_value=(np.array([884, 496]), field)), patch(
                'module.campaign_prototype.live.movement_anchor') as ring:
            np.testing.assert_array_equal(resolve_anchor(session, field, {'position': [10, 20]}), [884, 496])
        ring.assert_not_called()
        np.testing.assert_array_equal(session.anchor_reference[2], [10, 20])


if __name__ == '__main__':
    unittest.main()
