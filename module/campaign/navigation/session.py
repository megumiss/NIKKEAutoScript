"""生产会话与实验会话共享的镜头规划、停稳及视图收尾。"""

import numpy as np


class NavigationSession:
    """复用移动实现，只要求调用方提供 win、localizer、emit 与 preview。"""

    def plan(self, observation, target):
        """道路只检查连通性；平面地图走紧凑小地图闭环规划。

        用道路路径验证小队到 target 的连通性，并发布剩余道路长度。
        返回客户区落点；规划可能平移镜头，小队移动点击仍由 move 执行。
        """
        from module.campaign.navigation.camera_navigation import plan_world_move_compact

        _, path = self.localizer.route(np.asarray(observation['position']), np.asarray(target))
        self.emit(road_remaining=float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum()))
        return plan_world_move_compact(self, observation, target)

    def wait_stopped(self):
        """调用共享停稳检测，等待紧凑地图的道路和小队圆环稳定。

        无返回值；超时和身份异常向 navigate 传播，阻止在上一段运动未结束时叠加输入。
        """
        from module.campaign.navigation.camera_navigation import wait_for_squad

        wait_for_squad(self)

    def finish_view(self):
        """返回紧凑小地图并保存终点，保留人工复核的画面。

        把小地图恢复为紧凑状态，再通过 preview 保存终点客户区图像。
        用于到达或需要复核的终态，截图本身不构成拾取成功或战斗完成的证据。
        """
        from module.campaign.navigation import controls as goto

        goto.map_close(self.win)
        self.preview(goto.capture_client(self.win))
