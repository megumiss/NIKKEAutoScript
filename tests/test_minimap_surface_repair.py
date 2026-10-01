"""面恢复应填充内部缺测、去掉弱毛刺，同时保护真实间隙和不同高度。"""

import unittest

import cv2
import numpy as np

from dev_tools.minimap_repair import evidence
from dev_tools.minimap_surface_repair import surface_cut, visible_terrain


class SurfaceRepairTests(unittest.TestCase):
    def fixture(self):
        domain = np.ones((50, 70), bool)
        probability = np.full(domain.shape, .05)
        probability[8:42, 8:62] = .97
        ratios = np.ones(domain.shape)
        core = np.zeros_like(domain)
        core[15:35, 15:55] = True
        return domain, probability, ratios, core

    def test_fill_weak_internal_hole_and_trim_attached_sliver(self):
        domain, p, depth, core = self.fixture()
        p[22:26, 25:29] = .5
        core[22:26, 25:29] = False
        p[5:8, 20:22] = .55
        mask = surface_cut(domain, p, depth, core, p < .35)
        self.assertTrue(mask[22:26, 25:29].all())
        self.assertFalse(mask[5:8, 20:22].any())

    def test_real_gap_splits_surfaces_even_under_smoothing(self):
        domain, p, depth, core = self.fixture()
        p[:, 33:36] = .05
        mask = surface_cut(domain, p, depth, core, p < .35)
        self.assertFalse(mask[:, 33:36].any())
        count, _ = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
        self.assertEqual(count - 1, 2)

    def test_height_discontinuity_does_not_borrow_support(self):
        domain, p, depth, core = self.fixture()
        p[23:26, 25:28] = .5
        core[23:26, 25:28] = False
        same_layer = surface_cut(domain, p, depth, core, p < .35)
        depth[23:26, 25:28] = 1.4
        other_layer = surface_cut(domain, p, depth, core, p < .35)
        self.assertTrue(same_layer[23:26, 25:28].all())
        self.assertFalse(other_layer[23:26, 25:28].any())

    def test_supported_small_island_survives(self):
        domain = np.ones((40, 40), bool)
        p = np.full(domain.shape, .05)
        p[10:30, 10:30] = .98
        mask = surface_cut(domain, p, np.ones_like(p), np.zeros_like(domain), p < .35)
        self.assertTrue(mask[15:25, 15:25].all())

    def test_empty_domain(self):
        mask = np.zeros((3, 3), bool)
        self.assertFalse(surface_cut(mask, mask.astype(float), np.ones((3, 3)), mask, mask).any())

    def test_icons_are_unknown_but_dark_background_remains_observable(self):
        image = np.zeros((100, 100, 3), np.uint8)
        image[40:60, 40:60] = (180, 50, 240)
        visible = visible_terrain(image)
        self.assertFalse(visible[50, 50])
        self.assertTrue(visible[25, 25])
        meta = dict(origin=[0, 0], rotation=np.eye(2), projection=np.eye(3),
                    vertical_origin=[0, 0], camera=[[0, 0]])
        result = evidence([[50, 50], [25, 25]], [1, 1], [np.zeros((100, 100), np.uint8)],
                          meta, [0], [visible])
        np.testing.assert_array_equal(result['seen'], [0, 1])


if __name__ == '__main__':
    unittest.main()
