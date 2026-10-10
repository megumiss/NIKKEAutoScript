"""Bounded field-camera and single-click experiment; full evidence stays on disk."""
import argparse
import json
import time
from pathlib import Path

from . import settings, runtime

import cv2
import numpy as np

from . import adaptive
from .probe import goto, jacobian, project
from .arrow_anchor import sample_window_anchor



def save_image(tag, image):
    """同时保存原始证据与压缩预览，任一写盘失败都作为实验失败处理。

    tag 决定原始图和预览文件名，image 使用客户区 BGR 图像。
    通过统一写图接口保存，任一输出失败均向入口传播，避免证据不完整时记录成功。
    """
    runtime.write_image(str(settings.output / f'{tag}.png'), image)
    runtime.write_image(str(settings.output / f'{tag}_preview.jpg'), cv2.resize(image, (1066, 600)),
                [cv2.IMWRITE_JPEG_QUALITY, 78])


@runtime.command
def main():
    """执行观察、自动目标移动或指定手势，绑定观测并保存操作前后证据。

    根据参数选择观察、地图目标移动、显式点击或镜头拖动，目标移动复用共享规划和停稳检测。
    检查地图身份、观测来源和前台后执行，最终保存前后截图与 JSON；finally 负责释放窗口。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', required=True)
    parser.add_argument('--target', type=int, choices=range(1, 15), default=10)
    parser.add_argument('--pan', nargs=2, type=float)
    parser.add_argument('--click', nargs=2, type=int)
    parser.add_argument('--move', action='store_true', help='自动平移到所选地图目标并点击；与 --pan/--click 互斥。')
    parser.add_argument('--observe', type=int, default=0)
    parser.add_argument('--source-report', type=Path)
    settings.arguments(parser)
    args = parser.parse_args()
    settings.configure(args)
    if sum(bool(value) for value in (args.pan, args.click, args.move)) > 1:
        raise ValueError('Pan, explicit click and automatic move are mutually exclusive')
    if not 0 <= args.observe <= 300:
        raise ValueError('Observation budget must be within 0..300 seconds')
    receipt = settings.output / f'{args.tag}.json'
    if receipt.exists():
        raise FileExistsError(receipt)
    loc = adaptive.AdaptiveLocalizer()
    target = loc.targets[args.target]
    report = {'binding': loc.package.binding, 'tag': args.tag, 'target_number': args.target,
              'target': target.tolist(), 'movement_sent': False}
    win = runtime.Window(goto.ARGS)
    try:
        win.focus()
        if args.pan or args.click or args.move:
            goto.map_open(win, reset=args.move)
            before_roi = win.capture()
            report['before'] = loc.locate(before_roi, args.tag + '_before', require_player=args.move)
            if args.source_report:
                source = json.loads(args.source_report.read_text(encoding='utf-8'))
                if (source.get('status') != 'completed' or source.get('binding') != loc.package.binding
                        or source.get('target_number') != args.target
                        or not 0 <= time.time() - source.get('observed_at', 0) <= 120):
                    raise RuntimeError('Stale or mismatched camera plan')
                old = np.asarray(source['location']['roi_to_map'])
                current = np.asarray(report['before']['roi_to_map'])
                if np.linalg.norm(project(old, [243, 231]) - project(current, [243, 231])) > 12:
                    raise RuntimeError('Camera moved since the plan; no pan sent')
            if np.mean(goto.mr.terrain(before_roi) != goto.mr.terrain(win.capture())) > .03:
                raise RuntimeError('Map changed during localization; no input sent')
        goto.map_close(win)
        field = goto.capture_client(win)
        save_image(args.tag + '_before', field)
        if goto.battle_popup_score(field) > .8:
            raise RuntimeError('Battle popup; experiment stopped')
        x, y = win.gui.ClientToScreen(win.hwnd, (0, 0))
        if args.move:
            from .camera_navigation import plan_world_move, window_session, wait_for_squad
            loc.route(np.asarray(report['before']['position']), target)
            session = window_session(win, loc, report, args.tag)
            click = plan_world_move(session, report['before'], target)
            win.check()
            win.handler.mouse_click(x + round(click[0]), y + round(click[1]))
            report.update(movement_sent=True, click=click.tolist())
            wait_for_squad(session)
        if args.pan:
            dx, dy = args.pan
            a = (1000 - round(dx / 2), 500 - round(dy / 2))
            b = (1000 + round(dx / 2), 500 + round(dy / 2))
            if any(not (300 < px < 1450 and 220 < py < 820) for px, py in (a, b)):
                raise ValueError('Pan outside field interior')
            win.handler.mouse_swipe((x + a[0], y + a[1]), (x + b[0], y + b[1]))
            report['pan'] = args.pan
            runtime.pause(2)
        if args.click:
            cx, cy = args.click
            if not (300 < cx < 1450 and 220 < cy < 820):
                raise ValueError('Click outside field interior')
            win.check()
            if win.gui.GetForegroundWindow() != win.hwnd:
                raise RuntimeError('Focus lost before click')
            win.handler.mouse_click(x + cx, y + cy)
            report.update(movement_sent=True, click=args.click)
        if win.handler._failures:
            raise RuntimeError('Driver gesture failed')
        for elapsed in range(0, args.observe, 5):
            runtime.pause(5)
            frame = goto.capture_client(win)
            save_image(f'{args.tag}_t{elapsed + 5:03}', frame)
            score = goto.battle_popup_score(frame)
            print(json.dumps({'elapsed': elapsed + 5, 'battle_popup_score': round(score, 3)}), flush=True)
            if score > .8:
                raise RuntimeError('Battle popup while observing; stop')
        field = goto.capture_client(win)
        save_image(args.tag + '_field', field)
        goto.map_open(win, reset=args.move)
        observed = win.capture()
        report['location'] = loc.locate(observed, args.tag, require_player=args.move)
        report['observed_at'] = time.time()
        r = report['location']
        target_roi = project(np.linalg.inv(r['roi_to_map']), target)
        report['target_roi'] = target_roi.tolist()
        center = np.asarray(observed.shape[1::-1]) / 2
        camera_center = project(np.asarray(r['roi_to_map']), center)
        conversion = jacobian(np.asarray(r['roi_to_map']), center) @ np.linalg.inv(
            jacobian(loc.old_matrix, center))
        report['camera_center_map'] = camera_center.tolist()
        report['field_direction_estimate'] = (goto.A_INV @ np.linalg.solve(
            conversion, target - camera_center)).tolist()
        goto.map_close(win)
        if r['position_kind'] == 'squad':
            report['distance_to_target'] = float(np.linalg.norm(target - r['position']))
            _, path = loc.route(np.asarray(r['position']), target)
            report['road_length'] = float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())
            try:
                anchor, field = sample_window_anchor(win, goto.capture_client(win),
                                                     report.setdefault('anchor_evidence', {}))
                point = np.asarray(r['player_roi'])
                conversion = jacobian(np.asarray(r['roi_to_map']), point) @ np.linalg.inv(
                    jacobian(loc.old_matrix, point))
                report['anchor'] = anchor.tolist()
                report['target_field_estimate'] = (anchor + goto.A_INV @ np.linalg.solve(
                    conversion, target - np.asarray(r['position']))).tolist()
            except (RuntimeError, ValueError) as exc:
                report['anchor_error'] = str(exc)
        print(json.dumps(report), flush=True)
    finally:
        runtime.finish(win, receipt, report)


if __name__ == '__main__':
    main()
