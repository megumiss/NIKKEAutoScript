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


def screenshot_to_client(point, screenshot_size, client_size=(1776, 999)):
    """按等比例截图尺寸换算客户区像素；裁剪或黑边导致的比例差异必须另行建模。

    point 属于未裁剪截图，尺寸参数均按宽、高排列，返回对应客户区浮点像素。
    要求截图与客户区宽高比一致且点在截图内部；黑边、裁剪及非等比缩放必须另建变换。
    """
    size = np.asarray(screenshot_size, float)
    client = np.asarray(client_size, float)
    point = np.asarray(point, float)
    if (
        size.shape != (2,)
        or client.shape != (2,)
        or point.shape != (2,)
        or not np.isfinite([*size, *client, *point]).all()
        or min(*size, *client) <= 0
        or np.any(point < 0)
        or np.any(point >= size)
    ):
        raise ValueError('Invalid screenshot coordinate or size')
    if not np.isclose(size[0] / size[1], client[0] / client[1]):
        raise ValueError('Screenshot aspect ratio differs; crop/letterbox transform required')
    return point * client / size


project = transform


def jacobian(matrix, point):
    """以单位像素差分计算局部投影 Jacobian，只用于局部方向与尺度换算。

    在 point 沿两轴各偏移一个 ROI 像素，计算投影差分作为 2×2 局部换算矩阵。
    该近似仅适用于当前位置附近的方向和位移，不应取代远点的完整透视投影。
    """
    return np.column_stack([project(matrix, point + step) - project(matrix, point) for step in np.eye(2)])
