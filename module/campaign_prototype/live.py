"""One observable move towards a Wiki annotation; never enters battle or picks up items."""
import argparse
import json

from . import settings, runtime

import cv2
import numpy as np

from .adaptive import AdaptiveLocalizer
from .probe import goto, jacobian


def ground_anchor(image):
    """先找小队箭头，再限制搜索其下方蓝色或橙色地面圆环，以稳健边界中心作为地面点击锚点。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, 215), (179, 110, 255))
    white |= cv2.inRange(hsv, (5, 160, 190), (30, 255, 255))
    white[:200] = white[650:] = 0
    white[:, :670] = white[:, 1110:] = 0
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, _, stats, centers = cv2.connectedComponentsWithStats(white)
    heads = [centers[i] for i in range(1, count)
             if 20 < stats[i, 2] < 70 and 15 < stats[i, 3] < 70 and stats[i, 4] > 150]
    if not heads:
        raise RuntimeError(f'Expected one squad arrow, got {heads}')
    # The white arrow sits above the squad's bright hair and clothing.
    hx, hy = np.rint(min(heads, key=lambda point: point[1])).astype(int)
    mask = cv2.inRange(hsv, (85, 65, 180), (115, 255, 255))
    mask |= cv2.inRange(hsv, (5, 65, 180), (30, 255, 255))
    mask[:hy + 40] = mask[hy + 140:] = 0
    mask[:, :hx - 90] = mask[:, hx + 90:] = 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] > 35]
    ys, xs = np.nonzero(np.isin(labels, keep))
    if len(xs) < 150:
        raise RuntimeError('No reliable blue ground ring; stop for visual review')
    lo, hi = np.percentile(xs, [1, 99]), np.percentile(ys, [1, 99])
    if not (65 < lo[1] - lo[0] < 170 and 35 < hi[1] - hi[0] < 120):
        raise RuntimeError(f'Unreliable ground ring bounds: {lo}, {hi}')
    return np.array([lo.mean(), hi.mean()])


def surface_anchor(image):
    """在小队箭头下方拟合有完整角度证据的地面圆环，排除同色地灯与护栏。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, 215), (179, 110, 255))
    white |= cv2.inRange(hsv, (5, 160, 190), (30, 255, 255))
    white[:200] = white[650:] = 0
    white[:, :670] = white[:, 1110:] = 0
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, _, stats, centers = cv2.connectedComponentsWithStats(white)
    heads = [centers[i] for i in range(1, count)
             if 20 < stats[i, 2] < 70 and 15 < stats[i, 3] < 70 and stats[i, 4] > 150]
    if not heads:
        raise RuntimeError(f'Expected one squad arrow, got {heads}')
    # The white arrow sits above the squad's bright hair and clothing.
    hx, hy = np.rint(min(heads, key=lambda point: point[1])).astype(int)
    mask = cv2.inRange(hsv, (100, 130, 180), (125, 255, 255))
    mask |= cv2.inRange(hsv, (5, 65, 180), (30, 255, 255))
    mask[:hy + 40] = mask[hy + 140:] = 0
    mask[:, :hx - 90] = mask[:, hx + 90:] = 0
    ys, xs = np.nonzero(mask)
    points = np.column_stack([xs, ys]).astype(np.float32)
    if len(points) < 150:
        raise RuntimeError('No reliable ground ring')

    def score(ellipse):
        """以各方向的圆弧覆盖评分，避免一段明亮护栏压过完整但较暗的圆环。"""
        (cx, cy), (width, height), angle = ellipse
        if not (abs(cx - hx) < 30 and hy + 65 < cy < hy + 120
                and 95 < height < 125 and 60 < width < 90 and width < height
                and abs(angle - 90) < 12):
            return None
        rotation = cv2.getRotationMatrix2D((0, 0), angle, 1)[:, :2]
        relative = (points - [cx, cy]) @ rotation.T / [width / 2, height / 2]
        keep = np.abs(np.linalg.norm(relative, axis=1) - 1) * width / 2 < 1.5
        angles = np.arctan2(relative[keep, 1], relative[keep, 0])
        bins = np.bincount(np.minimum(15, ((angles + np.pi) * 16 / (2 * np.pi)).astype(int)), minlength=16)
        if np.count_nonzero(bins >= 3) < 13 or keep.sum() < 100:
            return None
        return int(np.minimum(bins, 12).sum()), keep

    rng = np.random.default_rng(14)
    candidates = []
    for _ in range(3500):
        ellipse = cv2.fitEllipse(points[rng.choice(len(points), 5, replace=False)])
        found = score(ellipse)
        if found is not None:
            candidates.append((*found, ellipse))
    if not candidates:
        raise RuntimeError('Ground ring is occluded or lacks angular support')
    best = max(candidates, key=lambda item: item[0])
    for _ in range(3):
        ellipse = cv2.fitEllipse(points[best[1]])
        found = score(ellipse)
        if found is None:
            raise RuntimeError('Unstable ground ring refinement')
        best = (*found, ellipse)
    center = np.asarray(best[2][0])
    rivals = [item for item in candidates if item[0] >= best[0] * .95]
    if any(np.linalg.norm(np.asarray(item[2][0]) - center) > 5 for item in rivals):
        raise RuntimeError('Ambiguous ground ring center')
    return center


@runtime.command
def main():
    """定位、选短路点并生成计划；仅显式 --move 时单击，之后重新定位，异常无条件释放。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', required=True)
    parser.add_argument('--target', type=int, choices=[13, 14], default=14)
    parser.add_argument('--move', action='store_true')
    parser.add_argument('--step', type=float, default=70)
    settings.arguments(parser)
    args = parser.parse_args()
    settings.configure(args)
    if not 0 < args.step <= 100:
        raise ValueError('Step must be within 0..100 map pixels')
    if (settings.output / f'{args.tag}_live.json').exists():
        raise FileExistsError('Use a new evidence tag')
    loc = AdaptiveLocalizer()
    win = runtime.Window(goto.ARGS)
    report = {}
    try:
        win.focus()
        if goto.battle_popup_score(goto.capture_client(win)) > .8:
            raise RuntimeError('Battle popup; no navigation click')
        goto.map_open(win)
        observed = win.capture()
        report['before'] = loc.locate(observed, args.tag + '_before')
        position = np.array(report['before']['position'])
        target = loc.targets[args.target]
        waypoint, _ = loc.route(position, target)
        delta = waypoint - position
        delta *= min(1, args.step / max(np.linalg.norm(delta), 1))
        win.focus()
        if np.mean(goto.mr.terrain(observed) != goto.mr.terrain(win.capture())) > .03:
            raise RuntimeError('Minimap changed during localization; no move')
        goto.map_close(win)
        field = goto.capture_client(win)
        anchor = ground_anchor(field)
        point = np.asarray(report['before']['player_roi'])
        conversion = jacobian(np.asarray(report['before']['roi_to_map']), point) @ np.linalg.inv(
            jacobian(loc.old_matrix, point))
        click = goto.movement_click(anchor, np.linalg.solve(conversion, delta))
        report.update(target=target.tolist(), distance_before=float(np.linalg.norm(target - position)),
                      waypoint=(position + delta).tolist(), anchor=anchor.tolist(), click=click.tolist())
        cv2.circle(field, tuple(np.rint(anchor).astype(int)), 6, (0, 255, 255), 2)
        cv2.drawMarker(field, tuple(np.rint(click).astype(int)), (0, 0, 255), cv2.MARKER_CROSS, 24, 2)
        runtime.write_image(str(settings.output / f'{args.tag}_plan.jpg'), field)
        print(json.dumps(report), flush=True)
        if args.move:
            win.check()
            if win.gui.GetForegroundWindow() != win.hwnd:
                raise RuntimeError('Focus lost; no move')
            x, y = win.gui.ClientToScreen(win.hwnd, (0, 0))
            win.handler.mouse_click(x + round(click[0]), y + round(click[1]))
            if win.handler._failures:
                raise RuntimeError('Driver click failed')
            report['movement_sent'] = True
            runtime.pause(5)
            field = goto.capture_client(win)
            runtime.write_image(str(settings.output / f'{args.tag}_after.png'), field)
            if goto.battle_popup_score(field) > .8:
                raise RuntimeError('Battle popup after move; stop')
            goto.map_open(win)
            report['after'] = loc.locate(win.capture(), args.tag + '_after')
            report['distance_after'] = float(np.linalg.norm(target - report['after']['position']))
            win.focus()
            goto.map_close(win)
            runtime.write_image(str(settings.output / f'{args.tag}_final.jpg'), goto.capture_client(win))
            print(json.dumps(report), flush=True)
    finally:
        runtime.finish(win, settings.output / f'{args.tag}_live.json', report)


if __name__ == '__main__':
    main()
