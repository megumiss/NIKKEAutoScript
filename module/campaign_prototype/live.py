"""One observable move towards a Wiki annotation; never enters battle or picks up items."""
import argparse
import json
from pathlib import Path

from . import settings, runtime

import cv2
import numpy as np

from .adaptive import AdaptiveLocalizer
from .probe import goto


def squad_arrow(image):
    """分别匹配白／橙箭头及高亮笔画，避免背景颜色混入同一形状掩码。

    在避开固定 UI 的主场景内搜索白色、橙色和高亮三种掩码，按多种模板尺度比较候选。
    最佳分数至少 0.72，且远处无相近得分竞争者才返回箭头中心；否则返回 None，地面补偿由周期采样完成。
    """
    template = cv2.imread(str(Path(__file__).parent / 'assets/squad_arrow_tpl.png'), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise RuntimeError('小队箭头模板缺失。')
    # 镜头平移后小队不一定在屏幕中央；搜索范围覆盖可点击场景并留出箭头动画余量。
    left, top, right, bottom = 250, 120, 1500, 880
    hsv = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    masks = [cv2.inRange(hsv, (0, 0, 215), (179, 110, 255)),
             cv2.inRange(hsv, (0, 0, 245), (179, 80, 255)),
             cv2.inRange(hsv, (5, 160, 190), (30, 255, 255))]
    candidates = []
    for scale in (.7, .75, .8, .85, .9, .95, 1., 1.05, 1.1):
        resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        for mask in masks:
            scores = cv2.matchTemplate(mask, resized, cv2.TM_CCOEFF_NORMED)
            for _ in range(2):
                _, score, _, (x, y) = cv2.minMaxLoc(scores)
                center = np.array([x + left + resized.shape[1] / 2, y + top + resized.shape[0] / 2])
                candidates.append((score, center))
                scores[max(0, y - 30):y + 31, max(0, x - 30):x + 31] = -1
    score, center = max(candidates, key=lambda candidate: candidate[0])
    if score < .72:
        return None
    if any(other_score > score * .95 and np.linalg.norm(point - center) > 30
           for other_score, point in candidates):
        return None
    return center


@runtime.command
def main():
    """定位并生成远点计划；显式 --move 时可平移镜头并单击，之后重新定位。

    默认生成目标计划和截图，--move 才执行镜头平移及一次小队点击；--step 可显式约束实验路点。
    点击前核对窗口和观测，移动后等待停稳并重新定位；证据写入按标签命名的文件，退出时释放控制。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', required=True)
    parser.add_argument('--target', type=int, choices=[13, 14], default=14)
    parser.add_argument('--move', action='store_true')
    parser.add_argument('--step', type=float, help='显式限制旧短移实验的地图步长；默认直接前往完整目标。')
    settings.arguments(parser)
    args = parser.parse_args()
    settings.configure(args)
    if args.step is not None and not 0 < args.step <= 100:
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
        goto.map_open(win, reset=True)
        observed = win.capture()
        report['before'] = loc.locate(observed, args.tag + '_before')
        position = np.array(report['before']['position'])
        target = loc.targets[args.target]
        waypoint, _ = loc.route(position, target)
        if args.step is None:
            waypoint = target
        else:
            delta = waypoint - position
            waypoint = position + delta * min(1, args.step / max(np.linalg.norm(delta), 1))
        win.focus()
        if np.mean(goto.mr.terrain(observed) != goto.mr.terrain(win.capture())) > .03:
            raise RuntimeError('Minimap changed during localization; no move')
        goto.map_close(win)
        field = goto.capture_client(win)
        from .arrow_anchor import sample_window_anchor
        anchor, field = sample_window_anchor(win, field, report.setdefault('anchor_evidence', {}))
        from .camera_navigation import plan_world_move, window_session, wait_for_squad, field_chart, target_projection
        session = window_session(win, loc, report, args.tag)
        if args.move:
            session.anchor_reference = (field.copy(), anchor, position.copy())
            session.anchor_reference_method = 'arrow_cycle'
            click = plan_world_move(session, report['before'], waypoint, anchor=anchor)
        else:
            _, click = target_projection(field_chart(loc, report['before'], anchor), report['before'], waypoint)
        field = goto.capture_client(win)
        report.update(target=target.tolist(), distance_before=float(np.linalg.norm(target - position)),
                      waypoint=waypoint.tolist(), anchor=anchor.tolist(), click=click.tolist())
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
            wait_for_squad(session)
            field = goto.capture_client(win)
            runtime.write_image(str(settings.output / f'{args.tag}_after.png'), field)
            if goto.battle_popup_score(field) > .8:
                raise RuntimeError('Battle popup after move; stop')
            goto.map_open(win, reset=True)
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
