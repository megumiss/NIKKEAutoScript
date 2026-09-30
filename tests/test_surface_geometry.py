"""验证共同俯视坐标与高度相关截图投影，防止高低道路误用同一映射。"""

import unittest

import numpy as np

from module.campaign_prototype.surface_geometry import SurfaceGeometry, orthographic_chart
from module.campaign_prototype.surface_motion import plan_map_click, project


class SurfaceGeometryTests(unittest.TestCase):
    def setUp(self):
        self.projection = np.array([[1.4, .3, 40], [.2, 1.7, 25], [.0001, .002, 1.]])
        self.vertical = np.array([700., 850.])
        self.cameras = np.array([[0., 0.], [80., -45.]])
        self.display = np.array([[0., -1., 1200], [1., 0., 200], [0., 0., 1.]])
        self.surfaces = {'ground': [0., 0., 0.], 'upper': [0., 0., .25], 'ramp': [.0001, -.0002, .15]}
        self.geometry = SurfaceGeometry(self.projection, self.vertical, self.cameras, self.surfaces, self.display)

    def test_heights_share_map_position_but_differ_in_source_view(self):
        point = project(self.display, [150., 300.])
        low = self.geometry.roi_point(1, 'ground', point)
        high = self.geometry.roi_point(1, 'upper', point)
        self.assertGreater(np.linalg.norm(low - high), 10)
        np.testing.assert_allclose(self.geometry.map_point(1, 'ground', low), point)
        np.testing.assert_allclose(self.geometry.map_point(1, 'upper', high), point)
        self.assertGreater(np.linalg.norm(self.geometry.map_point(1, 'ground', high) - point), 10)

    def test_sloped_surface_matches_perspective_and_camera_pan(self):
        world = np.array([[50., 80.], [150., 300.], [240., 100.]])
        mapped = project(self.display, world)
        for identifier, coefficients in self.surfaces.items():
            height = world @ np.asarray(coefficients[:2]) + coefficients[2]
            for frame, camera in enumerate(self.cameras):
                q = self.vertical + (world - self.vertical - camera) / (1 - height[:, None])
                expected = project(np.linalg.inv(self.projection), q)
                actual = self.geometry.roi_point(frame, identifier, mapped)
                np.testing.assert_allclose(actual, expected, atol=1e-8)
                np.testing.assert_allclose(self.geometry.map_point(frame, identifier, actual), mapped, atol=1e-8)

    def test_metric_chart_restores_orthogonal_equal_grid_axes(self):
        focal = 380.
        intrinsic = np.array([[focal, 0, 243], [0, focal, 231], [0, 0, 1.]])
        r1 = np.array([np.sqrt(.5), np.sqrt(.125), np.sqrt(.375)])
        r2 = np.array([-np.sqrt(.5), np.sqrt(.125), np.sqrt(.375)])
        image_projection = intrinsic @ np.column_stack([r1, r2, [0, 0, 900.]])
        unequal = np.array([[1.3, 0, 300], [0, .7, 200], [0, 0, 1.]])
        projection = unequal @ np.linalg.inv(image_projection)
        correction = orthographic_chart(projection, focal, (462, 486))
        square = np.array([[0., 0.], [100, 0], [0, 100], [100, 100]])
        image = project(image_projection, square)
        restored = project(correction @ projection, image)
        first, second = restored[1:3] - restored[0]
        self.assertAlmostEqual(float(first @ second), 0., places=7)
        self.assertAlmostEqual(np.linalg.norm(first), np.linalg.norm(second), places=7)
        np.testing.assert_allclose(restored[3] - restored[2], first, atol=1e-8)

    def test_unknown_surface_camera_and_nonorthographic_display_are_rejected(self):
        for frame, identifier in [(-1, 'ground'), (2, 'ground'), (True, 'ground'), (0, 'missing')]:
            with self.assertRaises(ValueError):
                self.geometry.roi_to_map(frame, identifier)
        display = self.display.copy()
        display[2, 0] = .01
        with self.assertRaisesRegex(ValueError, 'orthographic'):
            SurfaceGeometry(self.projection, self.vertical, self.cameras, self.surfaces, display)

    def test_surface_cannot_cross_camera_height(self):
        with self.assertRaisesRegex(ValueError, 'camera height'):
            self.geometry.roi_point(0, 'ramp', project(self.display, [10000., 0.]))

    def test_wiki_map_point_and_movement_use_same_surface_transform(self):
        source_point = np.array([100., 120.])
        target = self.geometry.map_point(1, 'upper', source_point)
        calibration = {'source_frame': 1, 'surface_id': 'upper', 'image_sha256': 'test-map',
                       'matrix': [[2., 0, 500], [0, 2., 250], [0, 0, 1.]],
                       'support': [[0, 0], [200, 0], [200, 200], [0, 200]]}
        registration = {'surface_id': 'upper', 'image_sha256': 'test-map',
                        'matrix': [[1., 0, 30], [0, 1., -10], [0, 0, 1.]],
                        'support': [[450, 200], [950, 200], [950, 700], [450, 700]]}
        click = plan_map_click(self.geometry, target, 'upper', 'test-map', calibration, registration)
        np.testing.assert_allclose(click, [730., 480.])
        with self.assertRaisesRegex(ValueError, 'different surfaces'):
            plan_map_click(self.geometry, target, 'ground', 'test-map', calibration, registration)
        with self.assertRaisesRegex(ValueError, 'different map'):
            plan_map_click(self.geometry, target, 'upper', 'new-map', calibration, registration)


if __name__ == '__main__':
    unittest.main()
