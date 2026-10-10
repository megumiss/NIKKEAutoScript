"""为分层基线补远隔重访闭环：用逐帧深度测量重访帧对的刚性位移，重解相机后重新融合到新目录。

python -m dev_tools.minimap_loops --package <local_parallax 包目录> --output <新目录>

只修正相机位置，不改逐帧深度和局部平面；输出仍是待几何复核的基线包，可继续做区域重绘。
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares

from dev_tools.minimap_layered import fuse_frames
from dev_tools.minimap_verify import LayeredPackage, revisit_pairs, summary


def sha(path):
    """绑定输入与代码，便于复现。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def measure_pairs(package, max_distance=70., min_gap=10, limit=300, spacing=20., tolerance=3.):
    """双向搜索每个重访帧对的整体平移；对齐后中位数超限或两向不一致的测量丢弃。"""
    candidates = revisit_pairs(package.camera, max_distance=max_distance, min_gap=min_gap,
                               limit=limit, spacing=spacing)
    accepted = []
    rejected = {'insufficient_contour': 0, 'misaligned': 0, 'inconsistent': 0}
    for first, second, distance, gap in candidates:
        forward, aligned_forward = package.best_shift(first, second, None)
        backward, aligned_backward = package.best_shift(second, first, None)
        if len(aligned_forward) < 300 or len(aligned_backward) < 300:
            rejected['insufficient_contour'] += 1
            continue
        medians = (float(np.median(aligned_forward)), float(np.median(aligned_backward)))
        if max(medians) > tolerance:
            rejected['misaligned'] += 1
            continue
        if np.linalg.norm(forward + backward) > tolerance:
            rejected['inconsistent'] += 1
            continue
        raw = np.concatenate([package.contour_distances(first, second, None),
                              package.contour_distances(second, first, None)])
        shift = (forward - backward) / 2
        accepted.append(dict(frames=[int(first), int(second)], camera_distance_px=distance, index_gap=int(gap),
                             shift_map_px=[float(value) for value in shift], raw_median_px=float(np.median(raw)),
                             aligned_median_px=max(medians), weight=1. / (1. + max(medians))))
    return accepted, rejected, len(candidates)


def solve_corrections(count, measurements, rotation, odometry_sigma=1., loop_sigma=2.):
    """位姿图松弛：相邻帧修正量的变化按里程计噪声约束，重访对按实测位移约束，首帧固定。

    地图位移换算到相机坐标用 shift @ rotation，因为地图坐标等于世界坐标 @ rotation.T。
    """
    pairs = np.asarray([item['frames'] for item in measurements])
    targets = np.asarray([item['shift_map_px'] for item in measurements]) @ np.asarray(rotation, float)
    weights = np.sqrt(np.asarray([item['weight'] for item in measurements]))[:, None] / loop_sigma

    def residual(flat):
        delta = flat.reshape(count, 2)
        odometry = (delta[1:] - delta[:-1]) / odometry_sigma
        loops = (delta[pairs[:, 0]] - delta[pairs[:, 1]] - targets) * weights
        return np.r_[odometry.ravel(), loops.ravel(), delta[0] * 10]

    fit = least_squares(residual, np.zeros(count * 2), loss='soft_l1', f_scale=2., max_nfev=300)
    delta = fit.x.reshape(count, 2)
    before = np.linalg.norm(targets, axis=1)
    after = np.linalg.norm(delta[pairs[:, 0]] - delta[pairs[:, 1]] - targets, axis=1)
    return delta, dict(converged=bool(fit.success), evaluations=int(fit.nfev),
                       loop_residual_before=summary(before), loop_residual_after=summary(after))


def close_loops(package_dir, output, max_distance=70., min_gap=10, limit=300):
    """读取基线包、测量并求解闭环修正、重新融合并写入新目录；输入包保持不变。"""
    source, output = Path(package_dir).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Use a new output directory outside the input package')
    package = LayeredPackage(source)
    if package.hash_mismatches:
        raise ValueError('Source frame hash mismatch')
    confidences = []
    for index in range(len(package.images)):
        with np.load(source / 'depth' / f'frame_{index:05d}.npz', allow_pickle=False) as data:
            confidences.append(data['confidence'])
    print(f'Measuring revisit pairs across {len(package.images)} frames', flush=True)
    measurements, rejected, candidates = measure_pairs(package, max_distance, min_gap, limit)
    if len(measurements) < 3:
        raise ValueError(f'Only {len(measurements)} consistent revisit measurements; cannot close loops')
    delta, solve = solve_corrections(len(package.images), measurements, package.rotation)
    camera = package.camera + delta
    norms = np.linalg.norm(delta, axis=1)
    print(json.dumps(dict(measured_pairs=len(measurements), rejected=rejected,
                          residual_before=solve['loop_residual_before']['median_px'],
                          residual_after=solve['loop_residual_after']['median_px'])), flush=True)
    print('Fusing frames with corrected cameras', flush=True)
    fused = fuse_frames(package.images, camera, package.projection, package.vertical, package.rotation,
                        package.ratios, confidences)
    output.mkdir(parents=True)
    for folder in ('source', 'depth'):
        shutil.copytree(source / folder, output / folder)
    for name in ('tracks.json', 'surface_model.json'):
        if (source / name).exists():
            shutil.copyfile(source / name, output / name)
    for name, image in (('map.png', fused['map_image']), ('reference.png', fused['reference'])):
        if not cv2.imwrite(str(output / name), image):
            raise OSError(f'Could not write {name}')
    np.savez_compressed(output / 'surface_data.npz', terrain_probability=fused['probability'],
                        coverage=fused['coverage'], frame_hits=fused['frame_hits'],
                        visible_frames=fused['visible_frames'], raw_probability=fused['raw_probability'],
                        source_frame=fused['source_frame'], source_roi=fused['source_roi'],
                        parallax_ratio=fused['parallax'], projection=package.projection, camera=camera,
                        rotation=package.rotation, vertical_origin=package.vertical, origin=fused['origin'])
    meta = dict(package.meta)
    # 重新融合后的包回到重绘之前的基线状态，旧的重绘与修复记录不再描述当前图像。
    for key in ('projected_redraw', 'map_render_mode', 'repair', 'surface_repair'):
        meta.pop(key, None)
    root = Path(__file__).resolve().parents[1]
    meta.update(camera=camera.tolist(), origin=[int(value) for value in fused['origin']],
                size=list(fused['probability'].shape[::-1]), crop=fused['bounds'],
                image_sha256=sha(output / 'map.png'), status='needs_geometry_review', navigation_ready=False,
                loop_closure=dict(method='rigid_revisit_pose_graph', input=str(source),
                                  input_image_sha256=package.image_sha256, candidate_pairs=candidates,
                                  measured_pairs=len(measurements), rejected=rejected,
                                  max_camera_distance_px=max_distance, min_index_gap=min_gap,
                                  odometry_sigma_px=1., loop_sigma_px=2.,
                                  correction=dict(median_px=float(np.median(norms)),
                                                  p90_px=float(np.percentile(norms, 90)), max_px=float(norms.max())),
                                  **solve, measurements=measurements,
                                  code_sha256={name: sha(root / name) for name in (
                                      'dev_tools/minimap_loops.py', 'dev_tools/minimap_layered.py',
                                      'dev_tools/minimap_verify.py')},
                                  limitations=[
                                      'Only camera positions change; per-frame depth and local planes are reused.',
                                      'Revisit shifts are rigid translations measured from the same depth; '
                                      'they are consistency evidence, not ground truth.',
                                      'Residual non-rigid disagreement after closure still needs depth or '
                                      'surface review.']))
    (output / 'map.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    (output / 'loop_closure_report.json').write_text(json.dumps(meta['loop_closure'], indent=2), encoding='utf-8')
    print(json.dumps(dict(output=str(output), measured_pairs=len(measurements),
                          correction_median_px=round(float(np.median(norms)), 2),
                          correction_max_px=round(float(norms.max()), 2),
                          residual_before=solve['loop_residual_before']['median_px'],
                          residual_after=solve['loop_residual_after']['median_px'], size=meta['size'])), flush=True)
    return meta['loop_closure']


def main():
    """只消费本机分层基线包，不连接游戏，不改写输入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-distance', type=float, default=70.)
    parser.add_argument('--min-gap', type=int, default=10)
    parser.add_argument('--limit', type=int, default=300)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    close_loops(args.package, args.output, args.max_distance, args.min_gap, args.limit)


if __name__ == '__main__':
    main()
