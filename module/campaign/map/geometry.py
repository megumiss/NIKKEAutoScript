"""地图与客户区坐标变换的共同数值校验，不读取实验数据。"""

import numpy as np


def matrix(value, shape=(3, 3)):
    """检查二维或三维方阵的形状、有限数值和可逆性，拒绝退化坐标变换。

    将输入转为浮点数组，默认要求 3×3，也可显式指定 2×2。
    形状错误、非有限元素或行列式接近零时抛出 ValueError；返回值可用于后续正逆投影。
    """
    result = np.asarray(value, dtype=float)
    if result.shape != shape or not np.isfinite(result).all() or abs(np.linalg.det(result)) < 1e-10:
        raise ValueError('Invalid coordinate transform')
    return result


def transform(value, point):
    """按齐次矩阵转换二维坐标并除以尺度，投影地平线附近不允许继续外推。

    point 是单个二维点，value 为可逆 3×3 齐次变换。
    返回完成齐次除法的二维数组；输入无效或分母接近零时拒绝，避免无穷大落点进入点击链。
    """
    point = np.asarray(point, dtype=float)
    if point.shape != (2,) or not np.isfinite(point).all():
        raise ValueError('Invalid coordinate point')
    projected = matrix(value) @ np.append(point, 1.0)
    if abs(projected[2]) < 1e-8:
        raise ValueError('Point lies on projection horizon')
    return projected[:2] / projected[2]


project = transform


def jacobian(matrix, point):
    """以单位像素差分计算局部投影 Jacobian，只用于局部方向与尺度换算。

    在 point 沿两轴各偏移一个 ROI 像素，计算投影差分作为 2×2 局部换算矩阵。
    该近似仅适用于当前位置附近的方向和位移，不应取代远点的完整透视投影。
    """
    return np.column_stack([project(matrix, point + step) - project(matrix, point) for step in np.eye(2)])
