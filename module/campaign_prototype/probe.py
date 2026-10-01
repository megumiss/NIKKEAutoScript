"""Bounded live navigation experiment using the annotated chapter 38 map."""
import argparse
import heapq
import json
from pathlib import Path

from . import settings, runtime

import cv2
import numpy as np

from . import goto



def project(matrix, point):
    """复用带有限数值及地平线检查的齐次投影，返回同一地图像素坐标。

    将二维点交给 map_package.transform 进行齐次投影。
    返回目标坐标系浮点像素，并继承矩阵、非有限输入和地平线检查。
    """
    from .map_package import transform
    return transform(matrix, point)


def jacobian(matrix, point):
    """以单位像素差分计算局部投影 Jacobian，只用于局部方向与尺度换算。

    在 point 沿两轴各偏移一个 ROI 像素，计算投影差分作为 2×2 局部换算矩阵。
    该近似仅适用于当前位置附近的方向和位移，不应取代远点的完整透视投影。
    """
    return np.column_stack([project(matrix, point + step) - project(matrix, point)
                            for step in np.eye(2)])


class Localizer:
    def __init__(self):
        """读取合格地图包、道路和目标，核对配套场景标定。

        加载 MapPackage 后提取道路概率、目标、投影与对应场景标定。
        道路阈值为 0.5，场景逆矩阵必须与 goto.A_INV 一致；加载失败不会创建游戏窗口。
        """
        from .map_package import MapPackage
        self.package = MapPackage()
        self.targets = self.package.targets
        self.matrix = self.package.projection
        self.size = self.package.warp_size
        self.road = (self.package.terrain >= .5).astype(np.float32)
        self.old_matrix = self.package.old_projection
        if not np.allclose(goto.A_INV, self.package.field_inverse):
            raise ValueError('Field calibration differs from the prototype')

    def locate(self, image, tag):
        """使用固定投影的道路相关定位小队，并以最佳分数及远处候选分差拒绝歧义。

        image 必须是 486×462 BGR 展开 ROI，要求恰好一个小队圆环并遮蔽图标和边框。
        粗到细平移搜索后保存截图、叠图和报告；IoU 小于 0.80、竞争峰差小于 0.10 或地图越界时抛错。
        """
        if image is None or image.shape != (462, 486, 3):
            raise ValueError('Expected a 486x462 BGR expanded minimap ROI')
        players, _ = goto.mr.detect_markers(image, np.eye(3))
        if len(players) != 1:
            raise RuntimeError(f'Expected one squad ring, got {players}')
        player = np.asarray(players[0])
        road = goto.mr.terrain(image)
        valid = np.full(road.shape, 255, np.uint8)
        valid[:4] = valid[-4:] = 0
        valid[:, :4] = valid[:, -4:] = 0
        valid[-35:, -75:] = 0
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        icons = cv2.inRange(hsv, (0, 0, 180), (179, 95, 255))
        red = cv2.inRange(hsv, (0, 65, 160), (10, 255, 255))
        red |= cv2.inRange(hsv, (155, 65, 160), (179, 255, 255))
        valid[cv2.dilate(icons | red, np.ones((7, 7), np.uint8)) > 0] = 0
        cv2.circle(valid, tuple(np.rint(player).astype(int)), 34, 0, -1)
        road[valid == 0] = 0

        def correlate(target, r, v):
            """由道路交集和有效域内并集计算平移 IoU，避免无效边框抬高分数。

            target 是道路底图，r 为投影道路，v 为可见区域掩码。
            用相关运算计算每个平移位置的交并比；分母至少为 1，空白和遮挡区域不会被当成道路支持。
            """
            intersection = cv2.matchTemplate(target, r, cv2.TM_CCORR)
            total = cv2.matchTemplate(target, v, cv2.TM_CCORR)
            return intersection / np.maximum(total + r.sum() - intersection, 1)

        r = cv2.warpPerspective(road, self.matrix, self.size).astype(np.float32) / 255
        v = cv2.warpPerspective(valid, self.matrix, self.size, flags=cv2.INTER_NEAREST).astype(np.float32) / 255
        r *= v
        scale, pad = 0.4, 600
        large = cv2.copyMakeBorder(self.road, pad, pad, pad, pad, cv2.BORDER_CONSTANT)
        reduced = lambda a: cv2.resize(a, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        scores = correlate(reduced(large), reduced(r), reduced(v))
        _, coarse, _, loc = cv2.minMaxLoc(scores)
        scores[max(0, loc[1]-40):loc[1]+41, max(0, loc[0]-40):loc[0]+41] = 0
        second = float(scores.max())
        start = np.maximum(0, np.rint(np.asarray(loc) / scale).astype(int) - 12)
        patch = large[start[1]:start[1]+self.size[1]+24, start[0]:start[0]+self.size[0]+24]
        _, score, _, loc = cv2.minMaxLoc(correlate(patch, r, v))
        offset = start + np.asarray(loc) - pad
        matrix = np.array([[1, 0, offset[0]], [0, 1, offset[1]], [0, 0, 1.]]) @ self.matrix
        position = project(matrix, player)
        report = {'position': position.tolist(), 'player_roi': player.tolist(), 'iou': score,
                  'coarse_iou': coarse, 'second_iou': second, 'offset': offset.tolist()}
        runtime.write_image(str(settings.output / f'{tag}_roi.png'), image)
        recovered = cv2.warpPerspective((self.road * 255).astype(np.uint8), np.linalg.inv(matrix),
                                        image.shape[1::-1], flags=cv2.INTER_NEAREST)
        overlay = image.copy()
        contours, _ = cv2.findContours(recovered, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(overlay, contours, -1, (0, 255, 255), 1)
        cv2.drawMarker(overlay, tuple(np.rint(player).astype(int)), (0, 0, 255), cv2.MARKER_CROSS, 16, 2)
        runtime.write_image(str(settings.output / f'{tag}_alignment.png'), overlay)
        (settings.output / f'{tag}_location.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        if score < .80 or coarse - second < .10:
            raise RuntimeError(f'Uncertain global localization: {report}')
        if not (0 <= position[0] < self.road.shape[1] and 0 <= position[1] < self.road.shape[0]):
            raise RuntimeError('Squad outside annotated map')
        return report

    def route(self, start, target):
        """在道路距离场上运行带净空代价的八邻域 A*，禁止斜穿墙角，再选择局部可直达路点。

        start、target 使用地图像素，先吸附到具备净空的 8px 网格节点，再运行八邻域 A*。
        返回局部可直达 waypoint 和完整网格路径；无连通路或无有效节点时失败，二维连通不证明跨层可通行。
        """
        if not np.isfinite([*start, *target]).all():
            raise ValueError('Nonfinite route coordinate')
        for point in (start, target):
            if np.any(np.asarray(point) < 0) or np.any(np.asarray(point) >= self.road.shape[::-1]):
                raise ValueError('Route coordinate outside map')
        step = 8
        clearance = cv2.distanceTransform(self.road.astype(np.uint8), cv2.DIST_L2, 5)
        grid = clearance[::step, ::step]
        ys, xs = np.nonzero(grid > 6)
        nodes = np.column_stack([xs, ys])
        if not len(nodes):
            raise RuntimeError('No traversable road nodes')
        nearest = lambda point: tuple(nodes[np.argmin(np.linalg.norm(nodes*step-point, axis=1))])
        source, goal = nearest(start), nearest(target)
        queue = [(0., source)]
        costs, parents = {source: 0.}, {}
        while queue:
            _, current = heapq.heappop(queue)
            if current == goal:
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                x, y = current[0] + dx, current[1] + dy
                if not (0 <= x < grid.shape[1] and 0 <= y < grid.shape[0]) or grid[y, x] <= 6:
                    continue
                if dx and dy and (grid[current[1], x] <= 6 or grid[y, current[0]] <= 6):
                    continue
                cost = costs[current] + np.hypot(dx, dy) * (1 + 8 / grid[y, x])
                node = (x, y)
                if cost >= costs.get(node, float('inf')):
                    continue
                costs[node], parents[node] = cost, current
                heapq.heappush(queue, (cost + np.hypot(x-goal[0], y-goal[1]), node))
        if goal not in costs:
            raise RuntimeError('No connected road route')
        path, node = [goal], goal
        while node != source:
            node = parents[node]
            path.append(node)
        path = np.asarray(path[::-1], float) * step
        waypoint = path[0]
        for candidate in [*path, target]:
            line = np.rint(np.linspace(start, candidate, max(2, int(np.linalg.norm(candidate-start))))).astype(int)
            if np.linalg.norm(candidate - start) > 100 or np.any(clearance[line[:, 1], line[:, 0]] < .5):
                break
            waypoint = candidate
        return waypoint, path

    def click(self, position, player_roi, waypoint, anchor):
        """比较新旧投影的局部 Jacobian，把地图短位移换回已有场景点击标定。

        position/waypoint 为地图坐标，player_roi 为小队 ROI 坐标，anchor 为客户区地面点。
        在小队附近比较两套投影的局部尺度，再由 movement_click 限制短移幅度；远点应使用完整镜头规划。
        """
        old_to_new = jacobian(self.matrix, np.asarray(player_roi)) @ np.linalg.inv(
            jacobian(self.old_matrix, np.asarray(player_roi)))
        offset = np.linalg.solve(old_to_new, waypoint - position)
        return goto.movement_click(anchor, offset)


@runtime.command
def main():
    """执行固定投影回放或有限步导航，点击前重新确认观测新鲜度及真实脚下锚点。

    离线模式读取 ROI 并输出路线诊断；现场模式使用自适应定位和共享镜头规划器。
    现场最多执行 --steps 次移动，每次检查观测新鲜度、等待停稳并重定位，finally 释放窗口和写回执。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', type=Path)
    parser.add_argument('--target', type=int, choices=[13, 14], default=14)
    parser.add_argument('--steps', type=int, default=1)
    parser.add_argument('--tag', default='run')
    settings.arguments(parser)
    args = parser.parse_args()
    settings.configure(args)
    if not 1 <= args.steps <= 8:
        raise ValueError('Choose 1..8 navigation steps')
    loc = Localizer()
    target = loc.targets[args.target]
    if args.offline:
        report = loc.locate(cv2.imread(str(args.offline)), args.tag)
        waypoint, path = loc.route(np.asarray(report['position']), target)
        report.update(target=target.tolist(), waypoint=waypoint.tolist(), route=path.tolist(),
                      click=loc.click(np.array(report['position']), report['player_roi'], waypoint,
                                      np.array([888., 499.])).tolist())
        print(json.dumps(report), flush=True)
        return
    from .adaptive import AdaptiveLocalizer
    from .camera_navigation import plan_world_move, window_session, wait_for_squad
    loc = AdaptiveLocalizer()
    target = loc.targets[args.target]
    win = runtime.Window(goto.ARGS)
    reports = []
    try:
        win.focus()
        for iteration in range(args.steps):
            tag = f'{args.tag}_{iteration:02d}'
            if goto.battle_popup_score(goto.capture_client(win)) > .8:
                raise RuntimeError('Battle popup appeared; stop collectible test')
            goto.map_open(win, reset=True)
            observed = win.capture()
            report = loc.locate(observed, tag)
            position = np.array(report['position'])
            waypoint, path = loc.route(position, target)
            report.update(target=target.tolist(), waypoint=waypoint.tolist(),
                          distance=float(np.linalg.norm(target-position)))
            reports.append(report)
            print(json.dumps(report), flush=True)
            if np.mean(goto.mr.terrain(observed) != goto.mr.terrain(win.capture())) > .03:
                raise RuntimeError('Minimap changed during localization; no move')
            goto.map_close(win)
            if report['distance'] < 12:
                report['status'] = 'near_annotation_requires_visual_check'
                runtime.write_image(str(settings.output / f'{tag}_near.png'), goto.capture_client(win))
                break
            session = window_session(win, loc, report, tag)
            click = plan_world_move(session, report, target)
            report.update(click=click.tolist())
            runtime.write_image(str(settings.output / f'{tag}_before.png'), goto.capture_client(win))
            win.check()
            if win.gui.GetForegroundWindow() != win.hwnd:
                raise RuntimeError('Lost game focus before movement')
            x, y = win.gui.ClientToScreen(win.hwnd, (0, 0))
            win.handler.mouse_click(x + int(round(click[0])), y + int(round(click[1])))
            if win.handler._failures:
                raise RuntimeError('Driver movement click failed')
            wait_for_squad(session)
            runtime.write_image(str(settings.output / f'{tag}_after.png'), goto.capture_client(win))
            if goto.battle_popup_score(goto.capture_client(win)) > .8:
                raise RuntimeError('Battle popup appeared after movement')
            goto.map_open(win, reset=True)
            after = loc.locate(win.capture(), tag + '_after')
            report['after'] = after
            report['displacement'] = (np.array(after['position']) - position).tolist()
            print(json.dumps({'after': after, 'displacement': report['displacement']}), flush=True)
            goto.map_close(win)
        runtime.write_image(str(settings.output / f'{args.tag}_final.png'), goto.capture_client(win))
    finally:
        runtime.finish(win, settings.output / f'{args.tag}_log.json', reports)


if __name__ == '__main__':
    main()
