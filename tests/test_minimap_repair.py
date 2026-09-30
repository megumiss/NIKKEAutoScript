"""局部补洞必须由多视角原始道路支持，不能跨越真实空白或混合高度。"""

import tempfile
from pathlib import Path
import unittest

import numpy as np

from dev_tools.minimap_repair import depth_proposals, evidence, qualified, repair, small_components


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.meta = dict(origin=[0, 0], rotation=np.eye(2), projection=np.eye(3),
                         vertical_origin=[0, 0], camera=np.array([[x, 0] for x in [0, 40, 80] * 2]))
        self.points = np.array([[160, 120]])
        self.roads = [np.full((240, 320), 255, np.uint8) for _ in range(6)]

    def test_real_road_and_blank_gap(self):
        self.assertTrue(qualified(evidence(self.points, [1], self.roads, self.meta, range(6)))[0])
        for road, camera in zip(self.roads, self.meta['camera']):
            x = 160 - camera[0]
            road[115:126, x - 5:x + 6] = 0
        self.assertFalse(qualified(evidence(self.points, [1], self.roads, self.meta, range(6)))[0])

    def test_validation_disagreement_rejects_training_result(self):
        for road in self.roads[3:]:
            road[:] = 0
        train = qualified(evidence(self.points, [1], self.roads, self.meta, range(3)))
        held = qualified(evidence(self.points, [1], self.roads, self.meta, range(3, 6)))
        self.assertTrue(train[0])
        self.assertFalse((train & held)[0])

    def test_no_camera_baseline_is_insufficient(self):
        self.meta['camera'][:] = 0
        self.assertFalse(qualified(evidence(self.points, [1], self.roads, self.meta, range(6)))[0])

    def test_empty_evidence(self):
        record = evidence(np.empty((0, 2)), [], self.roads, self.meta, range(6))
        self.assertEqual(len(qualified(record)), 0)

    def test_depth_proposals_reject_mixed_heights(self):
        seeds = np.ones((11, 11), bool)
        ratios = np.ones((11, 11), np.float32)
        self.assertTrue(np.isfinite(depth_proposals([[5, 5]], seeds, ratios)).all())
        ratios[:, 5:] = 1.3
        self.assertTrue(np.isnan(depth_proposals([[5, 5]], seeds, ratios)).all())

    def test_border_background_is_not_a_hole(self):
        mask = np.zeros((10, 10), bool)
        mask[0:2, 0:2] = True
        mask[5:7, 5:7] = True
        result = small_components(mask, 4, enclosed=True)
        self.assertEqual(result.sum(), 4)
        self.assertTrue(result[5, 5])

    def test_output_cannot_overwrite_or_nest_in_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                repair(folder, folder)
            with self.assertRaises(ValueError):
                repair(folder, Path(folder) / 'child')


if __name__ == '__main__':
    unittest.main()
