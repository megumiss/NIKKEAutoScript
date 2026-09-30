"""验证立体小地图重建的共同坐标、稠密多层匹配与数据保护。"""

from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from dev_tools.minimap_layered import (
    fusion_consistency, project, ratio_field, reconstruct, regularize_surfaces, surface_projection, surface_winners,
    sweep_surface,
)


class LayeredMapTests(unittest.TestCase):
    def test_surface_winners_do_not_leave_depth_holes_after_float_rounding(self):
        """真实采集中的双精度权重舍入后，也必须写入对应深度和来源。"""
        best = np.zeros((1, 3), np.float32)
        source = np.full(best.shape, -1, int)
        depth = np.full(best.shape, np.nan, np.float32)
        x, y = np.arange(3), np.zeros(3, int)
        weights = np.array([0.003355671311643646, .1, .7], np.float64)
        ratios = np.array([1., 1.2, 1.4], np.float32)
        selected = surface_winners(best, x, y, weights)
        source[y[selected], x[selected]] = selected
        depth[y[selected], x[selected]] = ratios[selected]
        np.testing.assert_array_equal(best > 0, source >= 0)
        np.testing.assert_array_equal(best > 0, np.isfinite(depth))
        np.testing.assert_allclose(depth[0], ratios)

    def test_surface_winners_choose_one_observation_per_pixel_and_preserve_stronger_history(self):
        """重复投影、同权重和后续较弱观测不能混写不同高度的元数据。"""
        best = np.array([[0., .8, 0.]], np.float32)
        x, y = np.array([0, 0, 0, 1, 1, 2]), np.zeros(6, int)
        weights = np.array([.2, .6, .6, .7, 0., 0.])
        selected = surface_winners(best, x, y, weights)
        np.testing.assert_array_equal(selected, [1])
        np.testing.assert_allclose(best, [[.6, .8, 0.]])
        selected = surface_winners(best, np.array([0, 0, 1]), np.zeros(3, int), np.array([.9, .4, .8]))
        np.testing.assert_array_equal(selected, [0])
        np.testing.assert_allclose(best, [[.9, .8, 0.]])

    def test_vertical_origin_recovers_common_coordinates_for_two_heights(self):
        """不同高度和相机位置对同一地面坐标的恢复必须一致，不能使用随意的图片中心。"""
        angle = .65
        rotation = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)],
                             [0, np.sin(angle), np.cos(angle)]])
        intrinsic = np.array([[400, 0, 160], [0, 400, 120], [0, 0, 1.]])
        ground = intrinsic @ np.column_stack([rotation[:, :2], [0, 0, 500]])
        # 避免第一轴消失点位于无穷远，与真实采集的斜向网格保持相同条件。
        yaw = np.array([[np.cos(.3), -np.sin(.3), 0], [np.sin(.3), np.cos(.3), 0], [0, 0, 1]])
        ground = ground @ yaw
        matrix = np.linalg.inv(ground)
        corrected, vertical, focal = surface_projection(matrix, [0, 0], [0, 0], (240, 320))
        self.assertAlmostEqual(focal, 400, places=6)
        world = np.array([[20., 30.], [40., 90.]])
        for ratio in (1., 1.4):
            for camera in (np.zeros(2), np.array([50, -20])):
                plane = vertical + ratio * (world - camera - vertical)
                roi = project(np.linalg.inv(corrected), plane)
                recovered = vertical + (project(corrected, roi) - vertical) / ratio + camera
                np.testing.assert_allclose(recovered, world, atol=1e-7)

    def test_dense_matching_separates_two_motion_scales(self):
        """同一次相机运动中两条道路的运动比例不同，稠密匹配应分别恢复。"""
        rng = np.random.default_rng(24)
        shape = (240, 320)
        camera = np.array([[0, 0], [40, 0], [-40, 0], [0, 40], [0, -40]], float)
        surfaces = []
        for x, ratio in [(60, 1.), (200, 1.4)]:
            image = np.zeros((*shape, 3), np.uint8)
            noise = cv2.GaussianBlur(rng.uniform(0, 1, (160, 60)).astype(np.float32), (3, 3), .5)
            image[40:200, x:x + 60] = np.stack([150 + noise * 90, 90 + noise * 70, 30 + noise * 40], axis=2)
            surfaces.append((image, ratio))
        images = []
        for position in camera:
            image = np.zeros((*shape, 3), np.uint8)
            for source, ratio in surfaces:
                affine = np.column_stack([np.eye(2), -position * ratio])
                image = np.maximum(image, cv2.warpAffine(source, affine, shape[::-1]))
            images.append(image)
        ratios, confidence = sweep_surface(0, images, camera, np.eye(3), [])
        for x, expected in [(60, 1.), (200, 1.4)]:
            sample = ratios[70:165, x + 12:x + 48]
            self.assertGreater(np.isfinite(sample).mean(), .8)
            self.assertLess(float(np.nanmedian(np.abs(sample - expected))), .025)
        self.assertTrue(np.all(confidence >= 0))

    def test_sparse_interpolation_does_not_average_height_jump(self):
        """两层之间的空白区域不得被一个跨层三角形填成坡道。"""
        support = [[10, 10, 1., 0, 0], [10, 80, 1., 0, 1],
                   [80, 10, 1.5, 0, 2], [80, 80, 1.5, 0, 3]]
        ratios, _ = ratio_field((100, 100), support)
        self.assertFalse(np.isfinite(ratios[40:60, 40:60]).any())

    def test_existing_data_and_nested_outputs_are_rejected(self):
        """离线重建不可覆盖输入、已有导出或标注。"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.mkdir()
            marker = source / 'scan.json'
            marker.write_text('preserve', encoding='utf-8')
            for output in (source, source / 'nested'):
                with self.assertRaises(ValueError):
                    reconstruct(source, output)
            self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve')

    def test_plane_fitting_preserves_ramp_and_rejects_depth_outliers(self):
        """真实坡度应保留，周期纹理造成的离散深度误配不应变成道路尖刺。"""
        yy, xx = np.indices((150, 150))
        expected = 1 + xx * .0008 + yy * .0003
        rng = np.random.default_rng(42)
        observed = expected + rng.normal(0, .004, expected.shape)
        outliers = rng.random(expected.shape) < .15
        observed[outliers] += .3
        result, _, _, records = regularize_surfaces(observed.astype(np.float32),
                                                     np.ones(expected.shape, np.float32),
                                                     np.full(expected.shape, 255, np.uint8), np.eye(3))
        self.assertGreater(np.isfinite(result).mean(), .8)
        self.assertLess(float(np.nanmedian(np.abs(result - expected))), .002)
        self.assertGreater(len(records), 0)

    def test_fusion_uses_empty_views_as_negative_evidence(self):
        """某帧误认出的道路必须被其他视野中的空白否决，不能仅累计正观测。"""
        base = np.zeros((100, 200), np.uint8)
        base[35:65, 80:130] = 255
        camera = np.array([[0., 0], [20, 0], [-20, 0]])
        roads = [cv2.warpAffine(base, np.column_stack([np.eye(2), -position]), (200, 100))
                 for position in camera]
        roads[0][35:55, 20:35] = 255
        probability, positive, seen = fusion_consistency(
            roads, camera, np.eye(3), np.zeros(2), np.eye(2), np.zeros(2), np.ones(base.shape, np.float32))
        self.assertEqual(probability[45, 100], 1)
        self.assertEqual(positive[45, 100], 3)
        self.assertGreaterEqual(seen[45, 25], 2)
        self.assertLessEqual(probability[45, 25], .5)


if __name__ == '__main__':
    unittest.main()
