"""Coordinate and model-contract checks independent of detection accuracy benchmarks."""

import unittest
from unittest.mock import patch

import cv2
import numpy as np

from module.campaign_prototype.detection import (
    LABELS, Detection, decode, detect_scene, letterbox, scene_mask, suppress,
)


class DecodeTests(unittest.TestCase):
    def test_rgb_padding_and_non_square_round_trip(self):
        image = np.zeros((462, 486, 3), np.uint8)
        image[:] = [10, 30, 240]
        tensor, scales, padding = letterbox(image, 640)
        np.testing.assert_allclose(tensor[0, :, 320, 320], np.array([240, 30, 10]) / 255)
        np.testing.assert_allclose(tensor[0, :, 0, 0], 114 / 255)
        box = np.array([145., 203., 194., 226.])
        center, size = (box[:2] + box[2:]) / 2, box[2:] - box[:2]
        output = np.zeros((1, 9, 1), np.float32)
        output[0, :4, 0] = [*(center * scales + padding), *(size * scales)]
        output[0, 8, 0] = .91
        found = decode(output, scales, padding, dict.fromkeys(LABELS, .5), image.shape)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].label, 'minimap_squad_ring')
        np.testing.assert_allclose(found[0].box, box, atol=.0001)

    def test_truncated_and_nonfinite_boxes_do_not_become_click_targets(self):
        output = np.zeros((1, 9, 5), np.float32)
        output[0, :4] = np.array([[20, 20, 30, 20], [0, 20, 30, 20],
                                  [20, 2, 30, 20], [20, 20, -5, 20], [np.nan, 20, 30, 20]]).T
        output[0, 6] = .99
        detections = decode(output, (1, 1), (0, 0), dict.fromkeys(LABELS, .5), (60, 60, 3))
        self.assertEqual(len(detections), 1)
        np.testing.assert_equal(detections[0].box, [5, 10, 35, 30])

    def test_incompatible_exports_and_scores_fail(self):
        for output in (np.zeros((1, 8400, 9)), np.zeros((1, 84, 8400)), np.ones((1, 9, 1)) * 2):
            with self.subTest(shape=output.shape), self.assertRaises(ValueError):
                decode(output, (1, 1), (0, 0), dict.fromkeys(LABELS, .5), (640, 640, 3))
        self.assertEqual(decode(np.zeros((1, 9, 0)), (1, 1), (0, 0),
                                dict.fromkeys(LABELS, .5), (640, 640, 3)), [])

    def test_nearby_normal_and_ex_are_not_suppressed_by_class_agnostic_nms(self):
        normal = Detection('minimap_enemy_normal', .95, (100, 100, 140, 130))
        ex = Detection('minimap_enemy_ex', .92, (110, 100, 150, 130))
        duplicate = Detection('minimap_enemy_normal', .8, (101, 101, 141, 131))
        result = suppress([normal, ex, duplicate])
        self.assertEqual(result, [normal, ex])

    def test_invalid_images_are_rejected(self):
        for image in (None, np.zeros((0, 5, 3), np.uint8), np.zeros((5, 5), np.uint8),
                      np.zeros((5, 5, 3), np.float32)):
            with self.assertRaises(ValueError):
                letterbox(image, 640)


class SceneTests(unittest.TestCase):
    def test_map_regions_are_masked_without_excluding_the_adjacent_scene(self):
        image = np.full((999, 1776, 3), 90, np.uint8)
        image[280:742, 644:1130] = cv2.cvtColor(np.uint8([[[100, 180, 120]]]), cv2.COLOR_HSV2BGR)[0, 0]
        result = scene_mask(image)
        self.assertFalse(result[250:790, 625:1150].any())
        self.assertFalse(result[120:295, :230].any())
        np.testing.assert_equal(result[350, 240], [90, 90, 90])
        np.testing.assert_equal(result[500, 1155], [90, 90, 90])
        np.testing.assert_equal(image[350, 640], [90, 90, 90])

    def test_tile_offsets_restore_the_client_coordinate_system(self):
        frame = np.zeros((999, 1776, 3), np.uint8)
        detections = [[], [], [], [], [], [Detection('scene_squad_arrow', .9, (100, 140, 150, 180))]]
        with patch('module.campaign_prototype.detection.detector') as model:
            model.return_value.predict.side_effect = detections
            result = detect_scene(frame)
        self.assertEqual(len(result), 1)
        np.testing.assert_equal(result[0].box, [1110, 400, 1160, 440])


if __name__ == '__main__':
    unittest.main()
