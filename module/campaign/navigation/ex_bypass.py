"""触发 EX 后的临时道路避让：只计算地图坐标，不操作游戏或修改资源路网。"""

import cv2
import numpy as np

from module.campaign.navigation.controls import SQUAD_REACH
from module.campaign.navigation.pathfinding import NoConnectedRoad, RoadLocalizer

EX_ASSOCIATION_LIMIT = 100.
EX_RETRY_LIMIT = 3
BYPASS_STEP = 120.


class ExBypassUnavailable(RuntimeError):
    """EX 位置不明确、目标落在避让区或没有安全绕路时停止，不回退为原路线反复点击。"""


class BypassRoad(RoadLocalizer):
    """仅在已切开道路副本上寻路，保持现有 A* 的道路边校验，不修改章节资源。"""

    def __init__(self, road):
        """绑定临时二值道路；有效光幕切口已由调用方施加，无额外跨层或电梯连接。"""
        self.road = road


def segment_clear(road, start, end):
    """逐像素校验直线段留在有效道路内；越界和非有限坐标直接拒绝。"""
    points = np.asarray([start, end], float)
    if points.shape != (2, 2) or not np.isfinite(points).all():
        return False
    line = np.rint(np.linspace(start, end, max(2, int(np.linalg.norm(points[1] - points[0]) * 2) + 1))).astype(int)
    if np.any(line < 0) or np.any(line >= np.asarray(road.shape[::-1])):
        return False
    return bool(np.all(road[line[:, 1], line[:, 0]] > 0))


class ExBypass:
    """按实际触发位置保存 EX 避让圆，每次移动都重算安全短段，直到原目标完成。

    距离均为底图像素。避让半径包含触发时小队到 EX 的距离及一个小队落点容差；
    同处重复触发时扩大半径，最多三次。记录只在当前会话有效，不改变地图或敌人清除状态。
    """

    def __init__(self):
        """创建空避让表；未触发 EX 的正常导航不走绕行逻辑。"""
        self.obstacles = []

    def encounter(self, squad, enemies):
        """从已重新定位的小队附近关联 EX，多个等距候选或缺失候选不凭原目标猜测。

        优先本帧 EX；被小队圆环遮挡时允许历史 EX 位置，但关联距离不得超过 100 像素；
        相距超过 25 像素且距离差不足 8 像素的两个候选视为歧义。
        """
        squad = np.asarray(squad, float)
        candidates = []
        for enemy in enemies:
            point = np.asarray(enemy.get('position'), float)
            if enemy.get('kind') != 'ex' or point.shape != (2,) or not np.isfinite(point).all():
                continue
            distance = float(np.linalg.norm(point - squad))
            if distance <= EX_ASSOCIATION_LIMIT:
                candidates.append((distance, point, enemy.get('visible', False)))
        current = [item for item in candidates if item[2]]
        candidates = [(distance, point) for distance, point, _ in (current or candidates)]
        candidates.sort(key=lambda item: item[0])
        if not candidates:
            raise ExBypassUnavailable('重新定位后，小队附近没有可关联的 EX 位置。')
        distance, point = candidates[0]
        if any(other - distance < 8 and np.linalg.norm(position - point) > 25
               for other, position in candidates[1:]):
            raise ExBypassUnavailable('小队附近有多个距离相近的 EX，无法确认刚关闭的关卡位置。')
        obstacle = next((item for item in self.obstacles
                         if np.linalg.norm(np.asarray(item['position']) - point) <= 50), None)
        if obstacle is None:
            obstacle = dict(position=point.tolist(), radius=0., encounters=0)
            self.obstacles.append(obstacle)
        obstacle['encounters'] += 1
        if obstacle['encounters'] > EX_RETRY_LIMIT:
            raise ExBypassUnavailable(f'同一 EX 绕行已达 {EX_RETRY_LIMIT} 次，仍重复触发。')
        obstacle['radius'] = max(distance + SQUAD_REACH, 40. + SQUAD_REACH,
                                 obstacle['radius'] + SQUAD_REACH)
        # 新观测与原避让圆同时保守覆盖，避免一次位置抖动把原触发区域重新开放。
        obstacle['radius'] = max(obstacle['radius'], distance + np.linalg.norm(
            point - np.asarray(obstacle['position'])) + SQUAD_REACH)
        return obstacle

    def check_target(self, target):
        """拒绝位于 EX 避让范围内的最终目标；到点判定前调用，不能用绕行宣告 EX 位置已完成。"""
        target = np.asarray(target, float)
        if target.shape != (2,) or not np.isfinite(target).all():
            raise ExBypassUnavailable('原目标坐标无效。')
        if any(np.linalg.norm(target - item['position']) <= item['radius'] for item in self.obstacles):
            raise ExBypassUnavailable('原目标位于 EX 避让范围内，不能绕行后继续点击该位置。')

    def waypoint(self, road, squad, target):
        """按小队—EX—目标关系计算下一处可直达绕行点，不能跨越避让圆或有效光幕。

        小队尚在避让圆内时只允许单调远离 EX 的退出线段，并检查退出后仍有路到目标；
        圆外先检查原道路路线，已不经过 EX 时恢复原目标直达；否则在临时切路上使用 A*，
        取最多 120 像素的可直达路点。目标被圆覆盖时明确拒绝，
        不把 EX 当作原目标完成，也不擅自挪动最终目标。所有候选不可行时交人工。
        """
        squad, target = np.asarray(squad, float), np.asarray(target, float)
        if (squad.shape != (2,) or target.shape != (2,) or not np.isfinite([squad, target]).all()
                or np.any(np.asarray([squad, target]) < 0)
                or np.any(np.asarray([squad, target]) >= np.asarray(road.shape[::-1]))):
            raise ExBypassUnavailable('小队或目标坐标无效，不能规划 EX 绕行。')
        circles = [(np.asarray(item['position']), item['radius']) for item in self.obstacles]
        self.check_target(target)
        safe = road.astype(np.uint8).copy()
        for point, radius in circles:
            cv2.circle(safe, tuple(np.rint(point).astype(int)), int(np.ceil(radius)), 0, -1)
        localizer = BypassRoad(safe)
        inside = [(point, radius) for point, radius in circles if np.linalg.norm(squad - point) <= radius + 1]
        try:
            if inside:
                point, radius = min(inside, key=lambda item: np.linalg.norm(squad - item[0]))
                candidates = []
                for angle in np.linspace(0, 2 * np.pi, 32, endpoint=False):
                    candidate = point + (radius + SQUAD_REACH) * np.array([np.cos(angle), np.sin(angle)])
                    direction = candidate - squad
                    if any(np.dot(squad - center, direction) < 0 for center, _ in inside):
                        continue
                    if not segment_clear(road, squad, candidate):
                        continue
                    line = np.linspace(squad, candidate, 128)
                    if any(np.min(np.linalg.norm(line - center, axis=1)) <= limit
                           for center, limit in circles if np.linalg.norm(squad - center) > limit + 1):
                        continue
                    if any(np.linalg.norm(candidate - center) <= limit for center, limit in circles):
                        continue
                    try:
                        _, path = localizer.route(candidate, target)
                    except (NoConnectedRoad, ValueError):
                        continue
                    if not segment_clear(safe, candidate, path[0]) or not segment_clear(safe, path[-1], target):
                        continue
                    cost = np.linalg.norm(direction) + np.linalg.norm(np.diff(path, axis=0), axis=1).sum()
                    candidates.append((cost, candidate))
                if candidates:
                    return min(candidates, key=lambda item: item[0])[1]
                raise ExBypassUnavailable('小队位于 EX 范围内，未找到可退出并继续到达目标的道路。')
            if segment_clear(safe, squad, target):
                return target
            _, original = BypassRoad(road).route(squad, target)
            original = np.vstack((squad, original, target))
            direct = np.linspace(squad, target, 256)
            if (all(np.min(np.linalg.norm(direct - center, axis=1)) > radius for center, radius in circles)
                    and all(segment_clear(safe, start, end) for start, end in zip(original[:-1], original[1:]))):
                return target
            _, path = localizer.route(squad, target)
            if not segment_clear(safe, path[-1], target):
                raise ExBypassUnavailable('原目标无法从避让后的道路安全到达。')
            candidates = [point for point in path if SQUAD_REACH < np.linalg.norm(point - squad) <= BYPASS_STEP
                          and segment_clear(safe, squad, point)]
            if candidates:
                return candidates[-1]
        except (NoConnectedRoad, ValueError) as exc:
            raise ExBypassUnavailable(f'避开 EX 后没有连通道路：{exc}') from exc
        raise ExBypassUnavailable('未找到有足够前进距离的 EX 绕行落点。')
