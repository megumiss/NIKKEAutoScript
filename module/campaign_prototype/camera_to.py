"""Pan only, using accepted localization reports; never send a movement click."""
import argparse
import json
import sys
import time

from . import settings, runtime

import numpy as np

from .probe import Localizer, goto, jacobian, project


@runtime.command
def main():
    """从新鲜且绑定正确地图的报告推算镜头方向，逐次启动受保护子进程，最多八次平移。"""
    p = argparse.ArgumentParser()
    p.add_argument('--target', type=int, required=True)
    p.add_argument('--from-tag', required=True)
    p.add_argument('--prefix', required=True)
    settings.arguments(p)
    args = p.parse_args()
    settings.configure(args)
    tag = args.from_tag
    loc = Localizer()
    target = loc.targets[args.target]
    for step in range(1, 10):
        report = json.loads((settings.output / f'{tag}.json').read_text())
        if (report.get('status') != 'completed' or report.get('binding') != loc.package.binding
                or report.get('target_number') != args.target
                or not 0 <= time.time() - report.get('observed_at', 0) <= 120):
            raise RuntimeError('Camera report failed or refers to another map package')
        matrix = np.asarray(report['location']['roi_to_map'])
        x, y = project(np.linalg.inv(matrix), target)
        if 100 < x < 400 and 100 < y < 330:
            print(json.dumps({'camera_ready': tag, 'target_roi': [x, y]}), flush=True)
            return
        if step > 8:
            raise RuntimeError('Camera pan budget exhausted')
        center = np.array([243., 231.])
        camera_center = project(matrix, center)
        conversion = jacobian(matrix, center) @ np.linalg.inv(jacobian(loc.old_matrix, center))
        delta = -goto.A_INV @ np.linalg.solve(conversion, target - camera_center)
        scale = min(1., 650 / max(abs(delta[0]), 1), 540 / max(abs(delta[1]), 1))
        delta = np.rint(delta * scale).astype(int)
        source_report = settings.output / f'{tag}.json'
        tag = f'{args.prefix}{step:02}'
        cmd = [sys.executable, '-X', 'utf8', '-m', 'module.campaign_prototype.run',
               *settings.child_arguments(), '--target', str(args.target),
               '--tag', tag, '--pan', str(delta[0]), str(delta[1]), '--source-report', str(source_report)]
        runtime.run_child(cmd, settings.output / f'{tag}.log')
        report = json.loads((settings.output / f'{tag}.json').read_text())
        print(json.dumps({'tag': tag, 'pan': delta.tolist(), 'target_roi': report['target_roi'],
                          'camera_center_map': report['camera_center_map'],
                          'iou': report['location']['iou']}), flush=True)


if __name__ == '__main__':
    main()
