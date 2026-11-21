"""章节道路距离计算与目标吸附约束，供正式目标规划和子目标选择复用。"""

import types

import numpy as np

from module.campaign.navigation.pathfinding import RoadLocalizer as FlatLocalizer, NoConnectedRoad

# 目标点允许偏离道路的上限；A* 会把场外点吸附到最近网格，必须先挡住跨墙、跨层的候选。
# 依据大地图圆环的短轴覆盖半径（goto.SQUAD_REACH，地图像素）：小队站在路上时圆环能触到路外
# 圆环半径内的机关落点。取 20 而不是满额 28，是为留出跨墙误吸附的余量，先保守观察误选情况。
ROAD_TOLERANCE = 20.0
# A* 会把端点吸附到最近的净空网格；吸附上限按圆环覆盖半径取（介于短轴 28 与长轴 40 之间），
# 吸附超过它说明目标不在可通行道路网格上。
SNAP_RADIUS = 32.0

def walking_distance(road, route_labels, start, target):
    """沿连通道路的行走距离；不连通或不可达时返回无穷大。

    road 为二值道路掩码，route_labels 为连通分区网格，无分区时为 None。
    路径来自平面原型的 A*，距离按路径长度计，比直线距离更能反映实际绕行；
    目标不在可通行网格上（或只有网格外的细路）时不报吸附点距离，按不可达处理。
    """
    shim = types.SimpleNamespace(road=road, route_labels=route_labels)
    try:
        _, path = FlatLocalizer.route(shim, np.asarray(start, float), np.asarray(target, float))
    except NoConnectedRoad:
        return float('inf')
    if np.linalg.norm(path[-1] - np.asarray(target, float)) > SNAP_RADIUS:
        return float('inf')
    return float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())
