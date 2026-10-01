"""Prepare a scene-reference click for visual review; does not control the game."""
import argparse
import json

from . import settings, runtime

import cv2
import numpy as np



@runtime.command
def main():
    """对 Wiki 场景与现场图做 SIFT/RANSAC 配准，生成待人工复核的落点估计，不发送输入。

    读取 Wiki 参考图和现场图，通过 SIFT 对应与 RANSAC 单应估计场景目标。
    输出匹配叠图及落点证据供人工复核；特征配准不等同于道路可通行验证，入口不控制鼠标。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=int, required=True)
    parser.add_argument('--tag', required=True)
    settings.arguments(parser)
    args = parser.parse_args()
    settings.configure(args)
    ref = cv2.imread(str(settings.reference_dir / f'{args.target:02}.png'))
    if ref is None:
        raise ValueError('Missing Wiki reference image')
    ref = cv2.resize(ref, (1776, 999))
    field = cv2.imread(str(settings.output / f'{args.tag}_field.png'))
    if field is None:
        raise ValueError('Missing field image')
    mask = np.zeros(ref.shape[:2], np.uint8)
    mask[180:850, 350:1580] = 255
    mask[360:550, 790:980] = 0
    sift = cv2.SIFT_create(nfeatures=6000)
    ka, da = sift.detectAndCompute(ref, mask)
    kb, db = sift.detectAndCompute(field, None)
    if da is None or db is None:
        raise RuntimeError('Scene descriptors missing')
    matches = cv2.BFMatcher().knnMatch(da, db, k=2)
    good = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < .72 * pair[1].distance]
    if len(good) < 4:
        raise RuntimeError('Insufficient scene matches')
    pa = np.float32([ka[m.queryIdx].pt for m in good])
    pb = np.float32([kb[m.trainIdx].pt for m in good])
    matrix, inliers = cv2.findHomography(pa, pb, cv2.RANSAC, 4)
    if matrix is None:
        raise RuntimeError('No scene homography')
    anchor = np.array([888., 492.], np.float32)
    click = cv2.perspectiveTransform(anchor[None, None], matrix)[0, 0]
    local = (np.linalg.norm(pa - anchor, axis=1) < 260) & inliers.ravel().astype(bool)
    residual = np.linalg.norm(cv2.perspectiveTransform(pa[None], matrix)[0] - pb, axis=1)
    report = {'target': args.target, 'source_tag': args.tag, 'reference_anchor': anchor.tolist(),
              'matches': len(good), 'inliers': int(inliers.sum()), 'local_inliers': int(local.sum()),
              'median_residual': float(np.median(residual[inliers.ravel() > 0])),
              'scene_click_estimate': click.tolist(), 'homography': matrix.tolist()}
    report['reviewable'] = bool(inliers.sum() >= 40 and local.sum() >= 6
                                and 300 < click[0] < 1450 and 220 < click[1] < 820)
    (settings.output / f'{args.tag}_scene_match.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    cv2.drawMarker(field, tuple(np.rint(click).astype(int)), (0, 0, 255), cv2.MARKER_CROSS, 32, 2)
    runtime.write_image(str(settings.output / f'{args.tag}_click_preview.jpg'), cv2.resize(field, (1066, 600)),
                [cv2.IMWRITE_JPEG_QUALITY, 80])
    print(json.dumps(report))


if __name__ == '__main__':
    main()
