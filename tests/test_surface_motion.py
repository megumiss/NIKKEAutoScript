"""局部区域坐标、独立验证及落点有效域的回归。"""

import unittest

import cv2
import numpy as np

from module.campaign_prototype.surface_motion import (
    contains, fit_calibration, fit_registration, plan_click, project, register_surface,
)


class SurfaceMotionTests(unittest.TestCase):
    def setUp(self):
        """构造两轴均有基线的局部道路观测和另三次未参与拟合的停靠。"""
        self.source = np.array([[0, 0], [100, 0], [0, 100], [100, 100], [20, 60], [80, 40]], float)
        self.matrix = np.array([[3, .2, 500], [-.3, 2, 300], [0, 0, 1.]])
        self.validation = np.array([[20, 30], [75, 60], [40, 80]], float)
        self.training = np.stack([self.source, project(self.matrix, self.source)], axis=1)
        self.heldout = np.stack([self.validation, project(self.matrix, self.validation)], axis=1)

    def test_calibration_predicts_independent_positions_and_refuses_outlier(self):
        """留出坏样本必须导致失败，不能回流到训练或按残差删除。"""
        result = fit_calibration(self.training, self.heldout)
        self.assertLess(result['validation_max_px'], 1e-6)
        np.testing.assert_allclose(project(result['matrix'], self.validation), self.heldout[:, 1])
        self.heldout[0, 1, 0] += 20
        with self.assertRaisesRegex(ValueError, 'independent validation'):
            fit_calibration(self.training, self.heldout)

    def test_collinear_or_extrapolated_calibration_is_rejected(self):
        """只有一条直路的样本不能证明另一轴的点击比例，区域外留出点也不能扩大使用域。"""
        self.training[:, 0, 1] = self.training[:, 0, 0]
        with self.assertRaisesRegex(ValueError, 'two directions'):
            fit_calibration(self.training, self.heldout)
        self.setUp()
        self.heldout[0, 0] = [110, 50]
        self.heldout[0, 1] = project(self.matrix, self.heldout[0, 0])
        with self.assertRaisesRegex(ValueError, 'leaves'):
            fit_calibration(self.training, self.heldout)

    def test_click_uses_target_surface_and_current_camera(self):
        """相同二维位置在两层上可以对应不同场景落点，不允许错用另一层的标定。"""
        low = dict(fit_calibration(self.training, self.heldout), surface_id='lower')
        high = dict(low, surface_id='upper', matrix=np.asarray(low['matrix']).copy())
        high['matrix'][1, 2] -= 100
        registration = {'matrix': [[1, 0, 30], [0, 1, 50], [0, 0, 1]],
                        'support': [[400, 150], [900, 150], [900, 700], [400, 700]]}
        first = plan_click([50, 50], 'lower', low, registration)
        second = plan_click([50, 50], 'upper', high, registration)
        np.testing.assert_allclose(first - second, [0, 100])
        with self.assertRaisesRegex(ValueError, 'different surfaces'):
            plan_click([50, 50], 'upper', low, registration)
        with self.assertRaisesRegex(ValueError, 'outside the calibrated'):
            plan_click([101, 50], 'lower', low, registration)
        registration['support'] = [[0, 0], [1, 0], [1, 1], [0, 1]]
        with self.assertRaisesRegex(ValueError, 'registration support'):
            plan_click([50, 50], 'lower', low, registration)

    def test_heldout_registration_detects_two_surfaces(self):
        """训练点共面、验证点属于另一高度时也必须拒绝整块道路配准。"""
        rng = np.random.default_rng(3)
        source = rng.uniform(0, 200, (30, 2))
        destination = source + [15, -8]
        result = fit_registration(source, destination)
        self.assertLess(result['validation_max_px'], 1e-4)
        destination[::3, 0] += 15
        with self.assertRaisesRegex(ValueError, 'held-out'):
            fit_registration(source, destination)

    def test_image_registration_and_textureless_rejection(self):
        """独立图像回放验证真实光流和区域配准，空白区域不能退回固定视野中心。"""
        rng = np.random.default_rng(14)
        reference = rng.integers(0, 256, (200, 200, 3), dtype=np.uint8)
        reference = cv2.GaussianBlur(reference, (3, 3), .5)
        current = cv2.warpAffine(reference, np.float32([[1, 0, 8], [0, 1, -5]]), (200, 200))
        polygon = [[25, 25], [170, 25], [170, 170], [25, 170]]
        result = register_surface(reference, current, polygon)
        np.testing.assert_allclose(project(result['matrix'], [100, 100]), [108, 95], atol=.2)
        with self.assertRaisesRegex(ValueError, 'corners'):
            register_surface(reference * 0, current * 0, polygon)

    def test_invalid_geometry_does_not_produce_click(self):
        """异常坐标、退化矩阵和地平线均在执行输入前失败。"""
        for matrix, point in [(np.zeros((3, 3)), [1, 1]), (np.eye(3), [np.nan, 0]),
                              ([[1, 0, 0], [0, 1, 0], [0, .1, 1]], [2, -10])]:
            with self.assertRaises(ValueError):
                project(matrix, point)
        with self.assertRaises(ValueError):
            contains([[0, 0], [0, 2], [2, 2]], [float('inf'), 0])



if __name__ == '__main__':
    unittest.main()
