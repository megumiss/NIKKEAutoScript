"""Match a live minimap with scale/perspective search before accepting its position."""
import json
from pathlib import Path

from . import settings, runtime

import cv2
import numpy as np

from .probe import Localizer, goto, project

from .match_coarse import PAD, SCALE, match, template_matrix


class AdaptiveLocalizer(Localizer):
    def locate(self, image, tag, require_player=True):
        """先搜索尺度与透视，再用受限 ECC 细化道路单应；低 IoU 或有远处竞争候选时拒绝定位。

        输入固定尺寸展开 ROI，搜索尺度与透视参数后对道路匹配进行受限 ECC 细化。
        返回 ROI→map、地图位置、质量分数及地图绑定；require_player=False 时允许视野中心，但 position_kind 不标记为小队。
        IoU 至少 0.85 且最佳与远处竞争候选差至少 0.10 才通过；失败也保留定位证据。
        """
        if image is None or image.shape != (462, 486, 3):
            raise ValueError('Expected a 486x462 BGR expanded minimap ROI')
        players, _ = goto.mr.detect_markers(image, np.eye(3))
        if len(players) > 1 or (require_player and len(players) != 1):
            raise RuntimeError(f'Expected one squad ring, got {players}')
        player = np.asarray(players[0] if len(players) == 1 else [image.shape[1] / 2, image.shape[0] / 2])
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
        if len(players) == 1:
            cv2.circle(valid, tuple(np.rint(player).astype(int)), 34, 0, -1)
        road[valid == 0] = 0
        target = cv2.copyMakeBorder(cv2.resize(self.road, None, fx=SCALE, fy=SCALE,
                                              interpolation=cv2.INTER_AREA), PAD, PAD, PAD, PAD,
                                   cv2.BORDER_CONSTANT)
        candidates = []

        def evaluate(a, b):
            """评估一组投影参数和全图平移峰，将候选变换还原为 ROI 到地图矩阵。

            在候选尺度 a、透视系数 b 下生成模板，并把相关峰换算回完整地图坐标。
            有效结果附加到外层 candidates，尺寸不适合或无匹配则跳过；每次计算前检查共享停止信号。
            """
            runtime.check_stop()
            matrix, size = template_matrix(self.matrix, a, b, road.shape)
            if min(size) < 10 or size[0] >= target.shape[1] or size[1] >= target.shape[0]:
                return
            record = match(road, valid, matrix, size, target)
            if record is None:
                return
            matrix = np.array([[1 / SCALE, 0, (record['translation'][0] - PAD) / SCALE],
                               [0, 1 / SCALE, (record['translation'][1] - PAD) / SCALE],
                               [0, 0, 1.]]) @ matrix
            record.update(a=float(a), b=float(b), matrix=matrix.tolist(),
                          position=project(matrix, player).tolist())
            candidates.append(record)

        for a in np.arange(.6, 1.61, .2):
            for b in np.arange(.5, 1.61, .1):
                evaluate(a, b)
        if not candidates:
            raise RuntimeError('No usable registration candidates')
        seed = max(candidates, key=lambda r: r['score'])
        for a in np.arange(max(.25, seed['a'] - .2), seed['a'] + .201, .04):
            for b in np.arange(max(.25, seed['b'] - .12), seed['b'] + .121, .02):
                evaluate(a, b)
        best = max(candidates, key=lambda r: r['score'])
        matrix = np.asarray(best['matrix'])
        # Camera follow changes perspective; refine only a globally disambiguated road match.
        source = cv2.warpPerspective((self.road * 255).astype(np.uint8), np.linalg.inv(matrix),
                                     road.shape[::-1])
        try:
            _, shift = cv2.findTransformECC(goto.mr.terrain(image).astype(np.float32) / 255,
                                            source.astype(np.float32) / 255,
                                            np.eye(3, dtype=np.float32), cv2.MOTION_HOMOGRAPHY,
                                            (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 200, 1e-6),
                                            valid, 7)
            ys, xs = np.nonzero(road)
            samples = np.column_stack([xs, ys]).astype(float)[::100]
            moved = cv2.perspectiveTransform(samples[None], shift.astype(float))[0]
            corrected = matrix @ shift
            if (np.max(np.linalg.norm(moved - samples, axis=1)) < 65
                    and np.linalg.norm(project(corrected, player) - project(matrix, player)) < 25):
                matrix = corrected
        except cv2.error:
            pass
        recovered = cv2.warpPerspective((self.road * 255).astype(np.uint8), np.linalg.inv(matrix),
                                        road.shape[::-1], flags=cv2.INTER_NEAREST)
        common = valid > 0
        first, second = road > 0, recovered > 0
        iou = float(((first & second) & common).sum() / max(1, ((first | second) & common).sum()))
        alternatives = [c['score'] for c in candidates
                        if np.linalg.norm(np.asarray(c['position']) - project(matrix, player)) > 130]
        rival = max([best['second'], *alternatives])
        report = {'position': project(matrix, player).tolist(),
                  'position_kind': 'squad' if len(players) == 1 else 'viewport_center',
                  'player_roi': player.tolist() if len(players) == 1 else None,
                  'iou': iou, 'coarse_iou': best['score'], 'second_iou': rival,
                  'binding': self.package.binding, 'roi_to_map': matrix.tolist(), 'a': best['a'], 'b': best['b']}
        runtime.write_image(str(settings.output / f'{tag}_roi.png'), image)
        overlay = image.copy()
        contours, _ = cv2.findContours(recovered, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(overlay, contours, -1, (0, 255, 255), 1)
        cv2.drawMarker(overlay, tuple(np.rint(player).astype(int)), (0, 0, 255), cv2.MARKER_CROSS, 16, 2)
        runtime.write_image(str(settings.output / f'{tag}_alignment.png'), overlay)
        (settings.output / f'{tag}_location.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        if iou < .85 or best['score'] - rival < .10:
            raise RuntimeError(f'Uncertain adaptive localization: {report}')
        p = report['position']
        if not (0 <= p[0] < self.road.shape[1] and 0 <= p[1] < self.road.shape[0]):
            raise RuntimeError('Squad outside annotated map')
        return report


@runtime.command
def main():
    """从保存的展开小地图离线定位并写配准证据，不取得游戏输入控制。

    读取 image 指定的离线 BGR 小地图，按 --tag 保存定位报告和叠图。
    地图包参数沿用 settings；图像不可读时失败，整个入口不会创建窗口或发送手势。
    """
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--tag', default='offline')
    settings.arguments(parser)
    args = parser.parse_args()
    settings.configure(args)
    image = cv2.imread(str(args.image))
    if image is None:
        raise ValueError('Unreadable minimap image')
    print(json.dumps(AdaptiveLocalizer().locate(image, args.tag)), flush=True)


if __name__ == '__main__':
    main()
