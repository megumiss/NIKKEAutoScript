"""原帧互验须区分大片同色重合与真实边界对应，保留分层投影。"""

import unittest

import cv2
import numpy as np

from dev_tools.minimap_layered import project
from dev_tools.minimap_region_redraw import check_view, plane_ratio, source_regions, source_to_other


class RegionRedrawTests(unittest.TestCase):
    def test_raw_transfer_matches_camera_equation_on_a_ramp(self):
        meta = dict(projection=[[1.2, .1, 20], [.05, 1.4, 30], [.0002, .0001, 1]],
                    camera=[[10, 20], [90, -30]])
        plane = dict(ratio_plane=[.1, -.2, 1.3], center=[120, 80], coordinate_scale=500)
        points = np.array([[30, 40], [150, 60], [220, 170]], float)
        q = project(meta['projection'], points)
        ratio = plane_ratio(plane, q)
        expected = project(np.linalg.inv(meta['projection']), q - ratio[:, None] * np.array([80, -50]))
        np.testing.assert_allclose(project(source_to_other(meta, 0, 1, plane), points), expected, atol=1e-8)

    def test_shifted_large_road_cannot_pass_on_iou_alone(self):
        first = np.zeros((180, 180), np.uint8)
        first[30:150, 30:150] = 255
        other = np.zeros_like(first)
        other[30:150, 34:154] = 255
        overlap, iou, _, edge_error = check_view(first, other, np.ones_like(first), np.eye(3), first >= 0)
        self.assertEqual(overlap, 1)
        self.assertGreater(iou, .9)
        self.assertGreater(edge_error, 2)
        _, _, _, aligned = check_view(first, other, np.ones_like(first),
                                      np.array([[1, 0, 4], [0, 1, 0], [0, 0, 1.]]), first >= 0)
        self.assertEqual(aligned, 0)

    def test_uniform_color_is_not_a_verified_correspondence(self):
        road = np.full((100, 100), 255, np.uint8)
        _, iou, _, boundary = check_view(road, road, np.ones_like(road), np.eye(3), road > 0)
        self.assertEqual(iou, 1)
        self.assertFalse(np.isfinite(boundary))

    def test_source_regions_keep_two_heights_separate_and_do_not_fill_raw_holes(self):
        road = np.zeros((100, 140), np.uint8)
        road[20:80, 10:65] = road[20:80, 65:120] = 255
        road[40:45, 35:40] = 0
        original = road.copy()
        labels = np.full(road.shape, -1, np.int16)
        labels[20:80, 10:65], labels[20:80, 65:120] = 0, 1
        ratios = np.full(road.shape, np.nan, np.float32)
        ratios[labels == 0], ratios[labels == 1] = 1., 1.4
        planes = [dict(ratio_plane=[0, 0, value], center=[0, 0], coordinate_scale=500) for value in [1., 1.4]]
        y, x = np.indices(road.shape)
        regions = list(source_regions(road, np.ones_like(road), {'ratio': ratios, 'local_surface': labels},
                                      planes, np.column_stack([x.ravel(), y.ravel()])))
        self.assertEqual(len(regions), 2)
        self.assertFalse(regions[0][2][50, 70])
        self.assertFalse(regions[1][2][50, 60])
        np.testing.assert_array_equal(road, original)


if __name__ == '__main__':
    unittest.main()
