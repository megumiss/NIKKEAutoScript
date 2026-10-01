"""分层缓存中局部道路的单应变换，供原帧绘制与截图坐标换算复用。"""

import numpy as np


def frame_plane_to_map(metadata, frame, plane):
    """将仿射视差平面严格展开为 ROI→map 单应，不重新拟合全局坐标。"""
    coefficients = np.asarray(plane['ratio_plane'], float)
    slope = coefficients[:2] / plane['coordinate_scale']
    offset = coefficients[2] - slope @ np.asarray(plane['center'])
    denominator = np.r_[slope, offset]
    vertical = np.asarray(metadata['vertical_origin'], float)
    camera = np.asarray(metadata['camera'][frame], float)
    local = np.array([[1, 0, -vertical[0]], [0, 1, -vertical[1]], [0, 0, 0]], float)
    local[:2] += np.outer(vertical + camera, denominator)
    local[2] = denominator
    canvas = np.eye(3)
    canvas[:2, :2] = metadata['rotation']
    canvas[:2, 2] = -np.asarray(metadata['origin'])
    result = canvas @ local @ np.asarray(metadata['projection'])
    if not np.isfinite(result).all() or abs(np.linalg.det(result)) < 1e-12:
        raise ValueError('Degenerate local surface projection')
    return result / np.linalg.norm(result)


def compose_registration(roi_to_map, query_to_roi):
    """截图先配准到原始帧，再进入绘制使用的同一局部变换。"""
    result = np.asarray(roi_to_map, float) @ np.asarray(query_to_roi, float)
    if result.shape != (3, 3) or not np.isfinite(result).all() or abs(np.linalg.det(result)) < 1e-12:
        raise ValueError('Invalid local registration')
    return result / np.linalg.norm(result)
