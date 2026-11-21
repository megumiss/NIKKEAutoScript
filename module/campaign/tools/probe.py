"""为自适应小地图定位器加载绑定地图、道路连通数据和共享场景标定。"""

import numpy as np

from module.campaign.tools import goto
from module.campaign.map.geometry import (
    transform as project,
)
from module.campaign.navigation.pathfinding import (
    RoadLocalizer as RoadLocalizer,
)


class Localizer(RoadLocalizer):
    """为自适应定位提供已验证地图包、标定和道路 A* 路径。

    投影与道路全部来自 MapPackage，所以构造成功即意味着地图数据已通过校验；
    场景逆矩阵还要与 goto.A_INV 一致，否则同一套像素位移会被换算成两种地图距离。
    加载失败不创建游戏窗口，保证数据不合格时不会发生任何输入。
    """

    def __init__(self):
        """读取合格地图包和道路，核对配套场景标定。

        加载 MapPackage 后提取道路概率、投影与对应场景标定。
        道路阈值为 0.5，场景逆矩阵必须与 goto.A_INV 一致；加载失败不会创建游戏窗口。
        """
        from module.campaign.tools.map_package import MapPackage
        self.package = MapPackage()
        self.matrix = self.package.projection
        self.road = (self.package.terrain >= .5).astype(np.float32)
        from module.campaign.map.connectivity import decode_labels
        self.route_labels = decode_labels(self.package.connectivity['effective'], self.road[::8, ::8].shape)
        self.old_matrix = self.package.old_projection
        if not np.allclose(goto.A_INV, self.package.field_inverse):
            raise ValueError('Field calibration differs from the prototype')
