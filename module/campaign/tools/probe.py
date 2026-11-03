"""Bounded live navigation experiment using the annotated chapter 38 map."""
import argparse
import json
from pathlib import Path

from module.campaign.tools import settings, runtime

import cv2
import numpy as np

from module.campaign.vision import perception

from module.campaign.tools import goto


from module.campaign.map.geometry import (
    jacobian as jacobian,
    transform as project,
)
from module.campaign.navigation.pathfinding import (
    NoConnectedRoad as NoConnectedRoad,
    RoadLocalizer as RoadLocalizer,
)


class Localizer(RoadLocalizer):
    """用固定投影在已验证地图包上定位小队，并提供道路 A* 路径。

    投影、道路与目标全部来自 MapPackage，所以构造成功即意味着地图数据已通过校验；
    场景逆矩阵还要与 goto.A_INV 一致，否则同一套像素位移会被换算成两种地图距离。
    加载失败不创建游戏窗口，保证数据不合格时不会发生任何输入。
    """

    def __init__(self):
        """读取合格地图包、道路和目标，核对配套场景标定。

        加载 MapPackage 后提取道路概率、目标、投影与对应场景标定。
        道路阈值为 0.5，场景逆矩阵必须与 goto.A_INV 一致；加载失败不会创建游戏窗口。
        """
        from module.campaign.tools.map_package import MapPackage
        self.package = MapPackage()
        self.targets = self.package.targets
        self.matrix = self.package.projection
        self.size = self.package.warp_size
        self.road = (self.package.terrain >= .5).astype(np.float32)
        from module.campaign.map.connectivity import decode_labels
        self.route_labels = decode_labels(self.package.connectivity['effective'], self.road[::8, ::8].shape)
        self.old_matrix = self.package.old_projection
        if not np.allclose(goto.A_INV, self.package.field_inverse):
            raise ValueError('Field calibration differs from the prototype')

    def locate(self, image, tag):
        """使用固定投影的道路相关定位小队，并以最佳分数及远处候选分差拒绝歧义。

        image 必须是 486×462 BGR 展开 ROI，要求恰好一个小队圆环并遮蔽图标和边框。
        粗到细平移搜索后保存截图、叠图和报告；IoU 小于 0.70、竞争峰差小于 0.10 或地图越界时抛错。
        """
        if image is None or image.shape != (462, 486, 3):
            raise ValueError('Expected a 486x462 BGR expanded minimap ROI')
        players, _ = perception.detect_markers(image, np.eye(3), mode='expanded')
        if len(players) != 1:
            raise RuntimeError(f'Expected one squad ring, got {players}')
        player = np.asarray(players[0])
        road = goto.terrain(image)
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
        if score < .70 or coarse - second < .10:
            raise RuntimeError(f'Uncertain global localization: {report}')
        if not (0 <= position[0] < self.road.shape[1] and 0 <= position[1] < self.road.shape[0]):
            raise RuntimeError('Squad outside annotated map')
        return report





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
    from module.campaign.tools.adaptive import AdaptiveLocalizer
    from module.campaign.navigation.camera_navigation import plan_world_move, window_session, wait_for_squad
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
            if np.mean(goto.terrain(observed) != goto.terrain(win.capture())) > .03:
                raise RuntimeError('Minimap changed during localization; no move')
            goto.map_close(win)
            if report['distance'] < goto.SQUAD_REACH:
                report['status'] = 'near_annotation_requires_visual_check'
                runtime.write_image(str(settings.output / f'{tag}_near.png'), goto.capture_client(win))
                break
            session = window_session(win, loc, report, tag, folder=settings.output, pause=runtime.pause)
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
