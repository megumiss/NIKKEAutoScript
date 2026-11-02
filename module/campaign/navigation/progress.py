"""依据冻结道路与目标检查真实移动的路线进展，不把镜头变化或观测次数当作推进。"""

import cv2
import numpy as np

from module.campaign.navigation.controls import SQUAD_REACH
from module.campaign.navigation.ex_bypass import BypassRoad
from module.campaign.navigation.pathfinding import NoConnectedRoad
from module.campaign.run_state import MAX_FAILURES


class NavigationStalled(RuntimeError):
    """同一目标连续真实移动未取得可靠路线进展，直接停止，不以回正重扫清空失败记录。"""


class MovementProgress:
    """按稳定目标保留路线历史最优值与连续无进展次数，EX 中断及重新规划均不清空。

    坐标和路程使用底图像素。普通与绕行阶段比较冻结目标在同一道路上的剩余路程；
    从 EX 圆内退出时比较冻结退出路点，并允许确认离开避让区且接上安全道路作为阶段完成。
    有效缩短使用现有 SQUAD_REACH 到点容差，不以任意位移或单帧目标标记漂移续期。
    """

    def __init__(self, target):
        """冻结本次目标坐标；只在新的稳定目标建立实例，不按镜头规划或 EX 续行重建。"""
        self.target = np.asarray(target, float).copy()
        if self.target.shape != (2,) or not np.isfinite(self.target).all():
            raise ValueError('路线进展目标必须是有限二维地图坐标。')
        self.road = None
        self.safe_road = None
        self.checkpoint = None
        self.exit_circles = []
        self.circles = []
        self.last_result = None
        self.best_remaining = None
        self.pending = False
        self.failures = 0

    @staticmethod
    def remaining(road, position, target):
        """返回含起终点吸附距离的真实道路路程，无路可走时不以直线距离代替。"""
        _, path = BypassRoad(road).route(position, target)
        return float(np.linalg.norm(np.asarray(position) - path[0])
                     + np.linalg.norm(np.diff(path, axis=0), axis=1).sum()
                     + np.linalg.norm(path[-1] - target))

    def prepare(self, road, position, waypoint, obstacles=()):
        """为下一次移动准备比较基准，重新计算的路点不替换尚未完成的同路网基准。

        EX 避让道路排除所有避让圆；小队在圆内时仅退出路段使用原道路，后续必须接上安全路网。
        仅路网或退出阶段真实变化时重建基准，不清空失败次数。纯规划不会产生一次进展判定。
        """
        if self.failures >= MAX_FAILURES:
            raise NavigationStalled(f'同一目标连续 {self.failures} 次移动未取得可靠路线进展。')
        position = np.asarray(position, float)
        circles = [(np.asarray(item['position'], float), float(item['radius'])) for item in obstacles]
        inside = [(point, radius) for point, radius in circles
                  if np.linalg.norm(position - point) <= radius + 1]
        safe = np.asarray(road, np.uint8).copy()
        for point, radius in circles:
            cv2.circle(safe, tuple(np.rint(point).astype(int)), int(np.ceil(radius)), 0, -1)
        metric_road = np.asarray(road, np.uint8) if inside else safe
        changed = (self.road is None or bool(inside) != bool(self.exit_circles)
                   or not np.array_equal(metric_road, self.road)
                   or not np.array_equal(safe, self.safe_road))
        if changed:
            self.road = metric_road.copy() if inside else safe
            self.safe_road = safe
            self.exit_circles = inside
            self.circles = circles
            self.checkpoint = np.asarray(waypoint if inside else self.target, float).copy()
            self.best_remaining = self.remaining(self.road, position, self.checkpoint)

    def sent(self):
        """成功发送与当前规划匹配的地面点击后，允许下一次可靠停稳定位检查一次进展。"""
        self.pending = self.road is not None

    def interrupt(self):
        """实际弹窗中断尚未走完的路段，不把途中位置记失败，也不清空已有失败历史。"""
        self.pending = False

    def observe(self, position):
        """消费一次已发送移动的可靠停稳位置，返回阶段、剩余路程及连续无进展次数。

        缩短必须优于历史最优值超过到点容差，来回走和小幅定位抖动不能反复刷新。
        已在目标到点范围内的有限触发试点交给原试点上限管理，不假装取得路线推进。
        达到既有 MAX_FAILURES 上限抛 NavigationStalled，拒绝下一次移动。
        """
        if not self.pending:
            return None
        self.pending = False
        position = np.asarray(position, float)
        previous = self.best_remaining
        try:
            remaining = self.remaining(self.road, position, self.checkpoint)
        except (NoConnectedRoad, ValueError):
            remaining = None
        exited = False
        if self.exit_circles and all(np.linalg.norm(position - point) > radius + 1
                                     for point, radius in self.circles):
            try:
                self.remaining(self.safe_road, position, self.target)
            except (NoConnectedRoad, ValueError):
                pass
            else:
                exited = True
        progressed = (exited or (remaining is not None and previous - remaining > SQUAD_REACH)
                      or (remaining is not None and remaining <= SQUAD_REACH < previous))
        if progressed:
            self.failures = 0
            self.best_remaining = remaining if remaining is not None else previous
        elif previous > SQUAD_REACH or remaining is None or remaining > SQUAD_REACH:
            self.failures += 1
        result = dict(stage='ex_exit' if self.exit_circles else 'route', progressed=progressed,
                      exited=exited, checkpoint=self.checkpoint.tolist(), position=position.tolist(),
                      previous_remaining=round(previous, 1),
                      remaining=None if remaining is None else round(remaining, 1), failures=self.failures)
        self.last_result = result
        if self.failures >= MAX_FAILURES:
            raise NavigationStalled(f'同一目标连续 {self.failures} 次移动未取得可靠路线进展；'
                                    f'阶段 {result["stage"]}，剩余路程 {result["remaining"]} 地图像素。')
        return result
