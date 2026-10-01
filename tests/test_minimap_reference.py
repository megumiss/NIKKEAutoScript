"""核对整帧实拍的几何换算、空白覆盖和输入保护。"""

from pathlib import Path
import tempfile
import unittest

import numpy as np

from dev_tools.minimap_layered import project
from dev_tools.minimap_reference import compose, frame_homography, frame_valid, inspect_holdout, render


class ReferenceTests(unittest.TestCase):
    def test_frame_homography_matches_reference_plane_and_inverse(self):
        projection = np.array([[1.4, .3, 40], [.2, 1.7, 25], [.0001, .002, 1.]])
        vertical = np.array([700., 850.])
        rotation = np.array([[.8, -.6], [.6, .8]])
        origin = np.array([-200., 150.])
        roi = np.array([[35., 45.], [100, 80], [240, 330], [390, 400]])
        q = project(projection, roi)
        for ratio in [1., 1.4]:
            for camera in [np.zeros(2), np.array([80., -45.])]:
                expected = (vertical + (q - vertical) / ratio + camera) @ rotation.T - origin
                matrix = frame_homography(projection, camera, vertical, rotation, origin, ratio)
                np.testing.assert_allclose(project(matrix, roi), expected, atol=1e-8)
                np.testing.assert_allclose(project(np.linalg.inv(matrix), expected), roi, atol=1e-8)

    def test_central_blank_observation_overwrites_peripheral_road(self):
        road = np.full((32, 32, 3), (186, 139, 59), np.uint8)
        blank = np.full_like(road, 10)
        valid = np.zeros((32, 32), np.uint8)
        valid[1:-1, 1:-1] = 1
        matrices = [np.array([[1., 0, -10], [0, 1., 0], [0, 0, 1.]]), np.eye(3)]
        reference, owner = compose([road, blank], matrices, valid, (32, 32), [0, 1])
        self.assertEqual(owner[16, 16], 1)
        np.testing.assert_array_equal(reference[16, 16], blank[16, 16])
        self.assertEqual(owner[16, 4], 0)
        np.testing.assert_array_equal(reference[16, 4], road[16, 14])

    def test_equal_weights_keep_first_source_and_excluded_frame_is_unused(self):
        images = [np.full((32, 32, 3), color, np.uint8) for color in [30, 100, 240]]
        valid = np.zeros((32, 32), np.uint8)
        valid[1:-1, 1:-1] = 1
        for selected in [[0, 1], [1, 0], [1]]:
            reference, owner = compose(images, [np.eye(3)] * 3, valid, (32, 32), selected)
            self.assertEqual(set(np.unique(owner)), {-1, selected[0]})
            np.testing.assert_array_equal(reference[16, 16], images[selected[0]][16, 16])
            self.assertFalse(reference[0].any())

    def test_holdout_reports_perfect_match_and_missing_coverage_separately(self):
        from dev_tools.minimap_reconstruct import terrain

        image = np.zeros((80, 80, 3), np.uint8)
        image[20:60, 25:55] = (186, 139, 59)
        mask = terrain(image)
        valid = np.ones((80, 80), np.uint8)
        owner = np.zeros_like(mask, np.int16)
        report = inspect_holdout([image], [np.eye(3)], valid, mask, owner, [0])
        self.assertEqual(report['road_iou_median'], 1.)
        self.assertEqual(report['contour_p95_px'], 0.)
        owner[:] = -1
        report = inspect_holdout([image], [np.eye(3)], valid, mask, owner, [0])
        self.assertIsNone(report['road_iou_median'])
        self.assertIsNone(report['contour_median_px'])
        self.assertEqual(report['frames'][0]['covered_fraction'], 0.)

    def test_frame_mask_excludes_controls_but_keeps_interior(self):
        mask = frame_valid((462, 486))
        self.assertFalse(mask[:15].any())
        self.assertFalse(mask[-50:, -90:].any())
        self.assertEqual(mask[200, 200], 1)

    def test_existing_or_nested_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            for output in [source, source / 'nested']:
                with self.assertRaises(ValueError):
                    render(source, output)


if __name__ == '__main__':
    unittest.main()
