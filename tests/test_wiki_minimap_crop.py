import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from dev_tools.wiki_collectible_match import MapMatcher
from dev_tools.wiki_minimap_crop import cropped_minimap


def screenshot(two_rings=False, road=True):
    image = np.full((430, 520, 3), (45, 65, 45), np.uint8)
    panel = image[31:311, 47:347]
    panel[:] = (50, 35, 20)
    if road:
        cv2.rectangle(panel, (15, 60), (285, 265), (210, 145, 65), -1)
    cv2.circle(panel, (115, 200), 16, (245, 240, 235), 2)
    if two_rings:
        cv2.circle(panel, (225, 130), 16, (245, 240, 235), 2)
    return image


class CroppedMinimapTests(unittest.TestCase):
    def test_offset_panel_and_off_center_squad_are_detected(self):
        panel, road, valid, player, box = cropped_minimap(screenshot())
        scale = np.array(panel.shape[1::-1]) / np.subtract(box[2:], box[:2])
        original = np.array(box[:2]) + player / scale
        self.assertLess(np.linalg.norm(original - [162, 231]), 2)
        self.assertGreater(np.count_nonzero(road), 1000)
        self.assertEqual(valid[tuple(np.rint(player)[::-1].astype(int))], 0)

    def test_multiple_squad_positions_are_rejected(self):
        with self.assertRaises(ValueError):
            cropped_minimap(screenshot(two_rings=True))

    def test_circle_without_minimap_road_support_is_rejected(self):
        with self.assertRaises(ValueError):
            cropped_minimap(screenshot(road=False))

    def test_report_matrix_maps_original_crop_pixels_after_resize(self):
        image = cv2.resize(screenshot(), None, fx=2, fy=2)
        matcher = MapMatcher.__new__(MapMatcher)
        matcher.surface_matcher = None
        matrix = np.array([[1.8, .1, 42], [.2, 2.1, 36], [.0002, .0001, 1.]])

        def match_panel(panel, road, valid, player, review):
            position = cv2.perspectiveTransform(player[None, None], matrix)[0, 0]
            return dict(position=position.tolist(), roi_to_map=matrix.tolist())

        matcher.match_panel = match_panel
        with patch('dev_tools.wiki_collectible_match.minimap_masks', side_effect=ValueError('layout')):
            result = matcher.match(image)
        recovered = cv2.perspectiveTransform(np.array([[result['player_center']]], float),
                                             np.array(result['roi_to_map']))[0, 0]
        np.testing.assert_allclose(recovered, result['position'], atol=1e-7)
        self.assertEqual(result['normalized_size'], [1040, 860])
        self.assertEqual(result['screenshot_layout'], 'cropped_minimap')

    def test_failed_surface_match_keeps_reason_without_inventing_a_transform(self):
        matcher = MapMatcher.__new__(MapMatcher)
        matcher.surface_matcher = Mock()
        matcher.surface_matcher.locate.return_value = dict(status='needs_review', reason='No correspondence')
        with patch('dev_tools.wiki_collectible_match.minimap_masks', side_effect=ValueError('layout')):
            result = matcher.match(screenshot())
        self.assertEqual(result['reason'], 'No correspondence')
        self.assertNotIn('roi_to_map', result)


if __name__ == '__main__':
    unittest.main()
