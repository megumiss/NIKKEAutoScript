"""验证局部视差实验的留出观测、平面兼容性和诊断输出边界。"""

import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from dev_tools.minimap_surface_fit import audit, compare_models, perspective_points, prepare_tracks


class SurfaceFitTests(unittest.TestCase):
    @staticmethod
    def scene(layered=False):
        """生成已知相机轨迹和固定高度点，覆盖双轴平移及未参与拟合的末两帧。"""
        camera = np.array([[i * 20, (i % 4) * 25] for i in range(12)], float)
        rng = np.random.default_rng(14)
        tracks = []
        for index, point in enumerate(rng.uniform(300, 700, (24, 2))):
            ratio = (1.0 if index < 12 else 1.4) if layered else 1.25
            tracks.append({'frames': list(range(len(camera))), 'plane': (point - ratio * camera).tolist()})
        return camera, tracks

    def test_planar_scene_needs_no_camera_or_perspective_change(self):
        """偏平面地图在两个模型下都保持原坐标，局部模型不能凭空制造高低层。"""
        camera, tracks = self.scene()
        original = copy.deepcopy(tracks)
        report = compare_models(camera, tracks)
        self.assertEqual(report['status'], 'diagnostic_only')
        for model in report['models']:
            self.assertTrue(model['converged'])
            self.assertLess(model['validation']['p95_px'], 1e-5)
            self.assertLess(model['camera_correction_max_px'], 1e-5)
            np.testing.assert_allclose(model['perspective_coefficients'], 0, atol=1e-8)
            for track in model['tracks']:
                self.assertAlmostEqual(track['parallax_ratio'], 1.25, places=5)
        self.assertEqual(tracks, original)

    def test_two_heights_predict_held_out_observations_without_averaging_layers(self):
        """真实双层运动应由局部模型预测，单一比例不能通过扭曲相机轨迹同时解释。"""
        camera, tracks = self.scene(layered=True)
        planar, local = compare_models(camera, tracks)['models']
        self.assertLess(local['validation']['p95_px'], 1e-4)
        self.assertGreater(planar['validation']['median_px'], 5)
        ratios = [track['parallax_ratio'] for track in local['tracks']]
        np.testing.assert_allclose(ratios[:12], 1., atol=1e-5)
        np.testing.assert_allclose(ratios[12:], 1.4, atol=1e-5)

    def test_validation_outlier_cannot_be_hidden_by_training_selection(self):
        """留出误差不得参与轨迹筛选或被低训练残差掩盖。"""
        camera, tracks = self.scene()
        before = prepare_tracks(camera, tracks, 48)
        tracks[0]['plane'][-1][0] += 40
        after = prepare_tracks(camera, tracks, 48)
        self.assertEqual(len(before), len(after))
        self.assertEqual(before[0]['initial_scale'], after[0]['initial_scale'])
        report = compare_models(camera, tracks)
        for model in report['models']:
            self.assertEqual(model['validation']['tracks'], 24)
            self.assertAlmostEqual(model['tracks'][0]['validation_max_px'], 40, places=4)
            self.assertEqual(model['supported_camera_validation']['tracks'], 0)
            self.assertLess(model['validation']['within_5px'], 1)
        self.assertEqual(report['status'], 'diagnostic_only')

    def test_projection_round_trip_and_horizon(self):
        """误差统计必须回到输入平面，投影分母不能跨越地平线。"""
        points = np.array([[20, 30], [600, 750]], float)
        center, coefficients = np.array([450, 450]), np.array([-.0001, .0002])
        adjusted = perspective_points(points, coefficients, center)
        np.testing.assert_allclose(perspective_points(adjusted, coefficients, center, inverse=True), points)
        with self.assertRaisesRegex(ValueError, 'horizon'):
            perspective_points([[0, 0]], [1, 1], [10, 10])

    def test_stationary_camera_and_sparse_tracks_are_inconclusive(self):
        """没有足够基线或道路轨迹时必须报告证据不足。"""
        camera, tracks = self.scene()
        for positions, observations in [(camera * 0, tracks), (camera, tracks[:3])]:
            report = compare_models(positions, observations)
            self.assertEqual(report['reason'], 'insufficient_tracks')
            self.assertEqual(report['models'], [])

    def test_nonfinite_or_out_of_range_geometry_is_rejected(self):
        """损坏相机或轨迹数据不能进入优化。"""
        camera, tracks = self.scene()
        with self.assertRaises(ValueError):
            compare_models(camera * np.nan, tracks)
        tracks[0]['frames'][-1] = len(camera)
        with self.assertRaises(ValueError):
            compare_models(camera, tracks)

    def test_audit_preserves_existing_outputs_and_source(self):
        """诊断不能覆盖已有结果，也不能把产物写进原始扫描目录。"""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source'
            source.mkdir()
            marker = source / 'scan.json'
            marker.write_text('preserve')
            for output in [source, source / 'audit']:
                with self.assertRaises(ValueError):
                    audit(source, output)
            self.assertEqual(marker.read_text(), 'preserve')


if __name__ == '__main__':
    unittest.main()
