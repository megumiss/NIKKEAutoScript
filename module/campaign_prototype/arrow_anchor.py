"""在固定客户区尺寸下消除小队箭头动画，并换算为原标定使用的地面锚点。"""

import numpy as np


def cycle_ground_anchor(points):
    """用覆盖两次动画的极值中点定位，拒绝缺帧、移动或不完整周期。"""
    points = np.asarray(points, float)
    if points.shape != (16, 2) or not np.isfinite(points).all():
        raise ValueError('箭头周期采样不完整。')
    if np.ptp(points[:, 0]) > 3:
        raise ValueError('采样期间小队横向位置变化。')
    halves = np.array_split(points[:, 1], 2)
    bounds = np.array([[part.min(), part.max()] for part in halves])
    amplitude = np.ptp(points[:, 1])
    if not 10 <= amplitude <= 18 or np.max(np.abs(bounds[0] - bounds[1])) > 3:
        raise ValueError('箭头动画周期不完整或小队仍在移动。')
    center = np.array([np.median(points[:, 0]), (points[:, 1].min() + points[:, 1].max()) / 2])
    # 1776×999 客户区，33 章白/橙箭头实测振幅均为 14px；白色地面圆环独立复核高度差约 90px。
    return center + [0, 90]


def sample_ground_anchor(session, field):
    """采样至少三秒；检查身份、停止请求和小地图静止后才发布地面位置。"""
    from . import goto
    from .live import squad_arrow

    if field.shape[:2] != (999, 1776):
        raise ValueError('箭头锚点仅适用于 1776×999 客户区。')
    points = []
    first_terrain = goto.mr.terrain(field[90:248, 32:211])
    for index in range(16):
        session.check()
        if index:
            session.pause(.2)
            field = goto.capture_client(session.win)
            session.identity(field)
        arrow = squad_arrow(field)
        if arrow is None:
            raise ValueError('未找到唯一的小队箭头。')
        current_terrain = goto.mr.terrain(field[90:248, 32:211])
        # 道路轮廓变化比角色动画可靠；采样期间发生行走或镜头移动时不使用箭头极值。
        if np.mean(first_terrain != current_terrain) > .03:
            raise ValueError('箭头采样期间小地图道路发生变化。')
        points.append(arrow)
    anchor = cycle_ground_anchor(points)
    session.emit(anchor_method='arrow_cycle', arrow_samples=np.asarray(points).tolist(),
                 ground_anchor=anchor.tolist())
    return anchor, field
