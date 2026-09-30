"""绘制和 Wiki 坐标组合必须使用同一局部投影，包括坡道与非零画布原点。"""

import unittest

import cv2
import numpy as np

from dev_tools.minimap_layered import project
from dev_tools.minimap_projected_redraw import warp_patch
from module.campaign_prototype.local_projection import compose_registration, frame_plane_to_map


class LocalProjectionTests(unittest.TestCase):
    def test_homography_equals_original_depth_formula(self):
        angle = .3
        meta = dict(projection=[[1.2, .1, 20], [.05, 1.4, 30], [.0002, .0001, 1]],
                    rotation=[[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]],
                    vertical_origin=[150, 300], camera=[[60, -30]], origin=[-100, 70])
        roi = np.array([[30, 40], [150, 60], [220, 170], [80, 210]], float)
        for coefficients in [[0, 0, 1.2], [.1, -.2, 1.3]]:
            plane = dict(ratio_plane=coefficients, center=[120, 80], coordinate_scale=500)
            q = project(meta['projection'], roi)
            ratio = ((q - plane['center']) / 500) @ np.array(coefficients[:2]) + coefficients[2]
            world = meta['vertical_origin'] + (q - meta['vertical_origin']) / ratio[:, None] + meta['camera'][0]
            expected = world @ np.array(meta['rotation']).T - meta['origin']
            matrix = frame_plane_to_map(meta, 0, plane)
            np.testing.assert_allclose(project(matrix, roi), expected, atol=1e-8)
            np.testing.assert_allclose(project(np.linalg.inv(matrix), expected), roi, atol=1e-8)

    def test_wiki_crop_scale_composes_with_drawing_transform(self):
        raw_to_map = np.array([[1.1, .2, 300], [-.1, 1.3, 180], [.0001, .0002, 1.]])
        wiki_to_raw = np.array([[2, 0, 30], [0, 2, 60], [0, 0, 1.]])
        points = np.array([[10, 20], [60, 80]], float)
        composed = compose_registration(raw_to_map, wiki_to_raw)
        np.testing.assert_allclose(project(composed, points), project(raw_to_map, project(wiki_to_raw, points)))

    def test_raw_background_is_projected_with_road(self):
        raw = np.zeros((20, 20), np.uint8)
        raw[3:17, 3:17] = 255
        raw[7:13, 7:13] = 0
        matrix = np.array([[2, 0, 10], [0, 2, 20], [0, 0, 1.]])
        patch = warp_patch(raw, matrix, (10, 20, 50, 60), cv2.INTER_NEAREST)
        self.assertEqual(patch[10, 10], 255)
        self.assertEqual(patch[20, 20], 0)

    def test_invalid_composition_is_rejected(self):
        with self.assertRaises(ValueError):
            compose_registration(np.zeros((3, 3)), np.eye(3))


if __name__ == '__main__':
    unittest.main()
