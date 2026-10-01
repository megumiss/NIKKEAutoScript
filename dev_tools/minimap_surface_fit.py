"""离线比较整图平面与局部视差模型，只输出诊断证据，不生成可导航地图。

python -m dev_tools.minimap_surface_fit --source <scan-directory> --output <new-directory>
"""

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

from dev_tools.minimap_reconstruct import reproject_scan, terrain


def collect_tracks(source, data, step=3, corners=120, distance=9):
    """跟踪道路轮廓并留出末两次观测；周期网格、边框与右下角控件不参与跟踪。"""
    masks = []
    for frame in data['frames']:
        image = cv2.imread(str(source / frame['file']))
        if image is None:
            raise OSError(f"Cannot read scan frame: {frame['file']}")
        masks.append(cv2.GaussianBlur(terrain(image), (5, 5), 1))
    if not masks:
        raise ValueError('Scan has no frames')
    shape = masks[0].shape
    if any(mask.shape != shape for mask in masks):
        raise ValueError('Scan frame dimensions differ')
    height, width = shape
    valid = np.full(shape, 255, np.uint8)
    valid[:15] = valid[-15:] = 0
    valid[:, :15] = valid[:, -15:] = 0
    valid[-50:, -90:] = 0
    camera = np.asarray([frame['position'] for frame in data['frames']], float)
    matrix = np.asarray(data['matrix'], float)
    tracks = []
    for start in range(0, len(masks) - 4, step):
        points = cv2.goodFeaturesToTrack(masks[start], corners, .04, distance, mask=valid, blockSize=7)
        if points is None:
            continue
        candidates = [[(start, point[0])] for point in points]
        active = np.arange(len(points))
        for frame in range(start + 1, min(len(masks), start + 9)):
            if len(points) == 0 or np.linalg.norm(camera[frame] - camera[frame - 1]) > data['cell_px'] * 3.6:
                break
            options = dict(winSize=(31, 31), maxLevel=4, criteria=(3, 60, .001))
            moved, ok, error = cv2.calcOpticalFlowPyrLK(masks[frame - 1], masks[frame], points, None, **options)
            back, reverse, _ = cv2.calcOpticalFlowPyrLK(masks[frame], masks[frame - 1], moved, None, **options)
            x, y = moved[:, 0].T
            keep = ((ok[:, 0] > 0) & (reverse[:, 0] > 0) & (error[:, 0] < 15)
                    & (np.linalg.norm(points[:, 0] - back[:, 0], axis=1) < .6)
                    & (x > 15) & (x < width - 15) & (y > 15) & (y < height - 15)
                    & ~((x > width - 90) & (y > height - 50)))
            active, points = active[keep], moved[keep]
            for index, point in zip(active, points):
                candidates[index].append((frame, point[0]))
        for track in candidates:
            if len(track) < 5:
                continue
            frames = [item[0] for item in track]
            screen = np.asarray([item[1] for item in track], np.float32)
            plane = cv2.perspectiveTransform(screen[None], matrix)[0]
            tracks.append({'frames': frames, 'screen': screen.tolist(), 'plane': plane.tolist()})
    return tracks


def prepare_tracks(camera, tracks, cell):
    """仅用训练观测选择有足够基线的轨迹，保证两种模型使用相同验证样本。"""
    selected = []
    for track in tracks:
        ids = np.asarray(track['frames'])
        points = np.asarray(track['plane'], float)
        if (len(ids) < 5 or not np.issubdtype(ids.dtype, np.integer) or np.any(ids < 0)
                or np.any(ids >= len(camera)) or np.any(np.diff(ids) <= 0)
                or points.shape != (len(ids), 2) or not np.isfinite(points).all()):
            raise ValueError('Invalid surface track')
        c = camera[ids[:-2]]
        centered = c - c.mean(axis=0)
        if np.linalg.norm(np.ptp(c, axis=0)) < cell * 2:
            continue
        scale = -np.sum(centered * (points[:-2] - points[:-2].mean(axis=0))) / np.sum(centered ** 2)
        intercept = np.mean(points[:-2] + scale * c, axis=0)
        error = np.linalg.norm(points[:-2] + scale * c - intercept, axis=1)
        if .6 < scale < 2 and np.median(error) < cell / 6:
            selected.append(dict(track, initial_scale=float(scale), initial_intercept=intercept.tolist()))
    return selected


def perspective_points(points, coefficients, center, inverse=False):
    """在同一参考点附近校正投影分母；拒绝跨越地平线，逆变换用于统一误差单位。"""
    delta = np.asarray(points, float) - center
    denominator = 1 + (-1 if inverse else 1) * (delta @ coefficients)
    if not np.isfinite(denominator).all() or np.any(denominator <= .1):
        raise ValueError('Surface projection crosses the usable horizon')
    return delta / denominator[..., None] + center


def error_summary(errors):
    """统计全部留出样本，不按拟合后误差挑选更有利的验证子集。"""
    values = np.asarray(errors, float)
    if not len(values):
        return {'tracks': 0, 'median_px': None, 'p90_px': None, 'p95_px': None, 'within_5px': None}
    return {'tracks': len(values), 'median_px': float(np.median(values)),
            'p90_px': float(np.percentile(values, 90)), 'p95_px': float(np.percentile(values, 95)),
            'within_5px': float(np.mean(values <= 5))}


def fit_model(camera, tracks, cell, center, local, return_geometry=False):
    """联合拟合相机修正与道路视差，末两次观测不参与优化；结果不是物理高度或导航坐标。"""
    count, total = len(camera), len(tracks)
    parameters = 3 if local else 2
    camera_size = count * 2
    observations = [(i, frame, point) for i, track in enumerate(tracks)
                    for frame, point in zip(track['frames'][:-2], track['plane'][:-2])]
    track_ids = np.array([item[0] for item in observations])
    frame_ids = np.array([item[1] for item in observations])
    points = np.array([item[2] for item in observations])
    anchors = np.array([track['frames'][0] for track in tracks])
    scales = np.array([track['initial_scale'] for track in tracks])
    shared_scale = float(np.median(scales))
    initial_points = np.array([track['initial_intercept'] for track in tracks]) - scales[:, None] * camera[anchors]
    initial = np.column_stack([initial_points, scales]) if local else initial_points
    seed = np.r_[np.zeros(camera_size), initial.ravel(), 0., 0.]

    def unpack(value):
        """分离相机、局部位置、视差比例及投影分母参数。"""
        correction = value[:camera_size].reshape(count, 2)
        params = value[camera_size:-2].reshape(total, parameters)
        ratios = params[:, 2] if local else np.full(total, shared_scale)
        return correction, params[:, :2], ratios, value[-2:]

    def residual(value):
        """保持弱相机运动先验，避免空白视野或低纹理道路使优化完全漂移。"""
        correction, origins, ratios, coefficients = unpack(value)
        positions = camera + correction
        predicted = origins[track_ids] - ratios[track_ids, None] * (
            positions[frame_ids] - positions[anchors[track_ids]])
        error = predicted - perspective_points(points, coefficients, center)
        return np.r_[error.ravel(), (np.diff(correction, axis=0) * .2).ravel(), correction[0] * 10]

    sparsity = lil_matrix((len(observations) * 2 + (count - 1) * 2 + 2, len(seed)), dtype=int)
    for index, (track, frame, _) in enumerate(observations):
        for axis in (0, 1):
            row = 2 * index + axis
            sparsity[row, 2 * frame + axis] = 1
            sparsity[row, 2 * anchors[track] + axis] = 1
            sparsity[row, camera_size + track * parameters + axis] = 1
            if local:
                sparsity[row, camera_size + track * parameters + 2] = 1
    for index in range(count - 1):
        for axis in (0, 1):
            row = len(observations) * 2 + index * 2 + axis
            sparsity[row, 2 * index + axis] = sparsity[row, 2 * (index + 1) + axis] = 1
    sparsity[:len(observations) * 2, -2:] = 1
    sparsity[-2, 0] = sparsity[-1, 1] = 1
    radius = max(1., float(np.abs(points - center).sum(axis=1).max()))
    bound = .4 / radius
    lower = [-np.inf, -np.inf, .5] if local else [-np.inf, -np.inf]
    upper = [np.inf, np.inf, 2.] if local else [np.inf, np.inf]
    limits = (np.r_[np.full(camera_size, -cell * 1.25), np.tile(lower, total), -bound, -bound],
              np.r_[np.full(camera_size, cell * 1.25), np.tile(upper, total), bound, bound])
    fit = least_squares(residual, seed, jac_sparsity=sparsity.tocsr(), bounds=limits,
                        loss='soft_l1', f_scale=2, max_nfev=300, ftol=1e-5, x_scale='jac')
    correction, origins, ratios, coefficients = unpack(fit.x)
    positions = camera + correction
    support = np.bincount(frame_ids, minlength=count)
    details, all_errors, supported_errors = [], [], []
    for index, track in enumerate(tracks):
        ids = np.asarray(track['frames'])
        predicted = origins[index] - ratios[index] * (positions[ids] - positions[ids[0]])
        predicted = perspective_points(predicted, coefficients, center, inverse=True)
        error = np.linalg.norm(predicted - track['plane'], axis=1)
        validation_error = float(error[-2:].max())
        supported = bool(np.all(support[ids[-2:]] >= 4))
        details.append({'frames': track['frames'], 'parallax_ratio': float(ratios[index]),
                        'training_median_px': float(np.median(error[:-2])),
                        'validation_max_px': validation_error, 'camera_supported': supported})
        all_errors.append(validation_error)
        if supported:
            supported_errors.append(validation_error)
    report = {'model': 'local_parallax' if local else 'shared_plane', 'converged': bool(fit.success),
            'evaluations': fit.nfev, 'validation': error_summary(all_errors),
            'supported_camera_validation': error_summary(supported_errors),
            'camera_correction_max_px': float(np.linalg.norm(correction, axis=1).max()),
            'perspective_coefficients': coefficients.tolist(), 'tracks': details}
    if return_geometry:
        report['geometry'] = {'camera': positions.tolist(), 'origins': origins.tolist(),
                              'ratios': ratios.tolist(), 'anchors': anchors.tolist(), 'center': center.tolist()}
    return report


def compare_models(camera, tracks, cell=48., center=(500., 500.)):
    """用完全相同的训练／留出轨迹比较两种模型；证据不足和优化失败都不能成为地图合格证明。"""
    camera = np.asarray(camera, float)
    center = np.asarray(center, float)
    if (camera.ndim != 2 or camera.shape[1] != 2 or not np.isfinite(camera).all()
            or not np.isfinite(cell) or cell <= 0 or center.shape != (2,) or not np.isfinite(center).all()):
        raise ValueError('Invalid camera geometry')
    selected = prepare_tracks(camera, tracks, cell)
    report = {'status': 'diagnostic_only', 'input_tracks': len(tracks), 'selected_tracks': len(selected),
              'error_units': 'input rectified projection pixels',
              'validation': 'last two observations of each selected track; shared camera is fitted from other tracks',
              'limitations': ['Tracks can overlap and are not statistically independent.',
                              'Predictions do not verify dense road geometry, layer connectivity or field clicks.']}
    if len(selected) < 20:
        return dict(report, reason='insufficient_tracks', models=[])
    report['models'] = [fit_model(camera, selected, cell, center, local) for local in (False, True)]
    report['reason'] = 'model_comparison_completed' if all(m['converged'] for m in report['models']) else 'fit_incomplete'
    return report


def audit(source, output):
    """读取完成扫描及其已接受投影，输出到新目录，并绑定输入摘要以便复现。"""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Use a new output directory outside the scan; preserve existing data')
    scan_path = source / 'scan.json'
    raw = scan_path.read_bytes()
    data = json.loads(raw)
    if data.get('version') != 2 or data.get('status') not in ('roads_exhausted', 'raster_closed'):
        raise ValueError('Expected a completed version 2 scan')
    hashes = {'scan.json': hashlib.sha256(raw).hexdigest()}
    registration = source / 'registration.json'
    projection_source = 'scan.json'
    if registration.exists():
        content = registration.read_bytes()
        hashes['registration.json'] = hashlib.sha256(content).hexdigest()
        recovery = json.loads(content).get('projection_recovery', {})
        if recovery.get('status') == 'accepted':
            width, height = np.subtract(data['roi'][2:], data['roi'][:2])
            data, _ = reproject_scan(data, np.asarray(recovery['matrix']), recovery['warp_size'], (height, width))
            projection_source = 'accepted projection recovery'
    for frame in data['frames']:
        path = (source / frame['file']).resolve()
        if not path.is_relative_to(source):
            raise ValueError('Frame path leaves the scan directory')
        hashes[frame['file']] = hashlib.sha256(path.read_bytes()).hexdigest()
    tracks = collect_tracks(source, data)
    report = compare_models([f['position'] for f in data['frames']], tracks, data['cell_px'],
                            np.asarray(data['warp_size']) / 2)
    report.update(source=str(source), chapter=data.get('chapter'), source_sha256=hashes,
                  projection_source=projection_source, projection=data['matrix'])
    if any(hashlib.sha256((source / name).read_bytes()).hexdigest() != digest for name, digest in hashes.items()):
        raise RuntimeError('Scan changed during audit; rerun against a completed snapshot')
    output.mkdir(parents=True)
    (output / 'tracks.json').write_text(json.dumps(tracks, indent=2), encoding='utf-8')
    (output / 'surface_fit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    """仅运行离线实验，不导出 map.json、修改标注或取得游戏输入控制。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    report = audit(args.source, args.output)
    print(json.dumps({key: value for key, value in report.items() if key not in ('source_sha256', 'models')},
                     ensure_ascii=False))
    for model in report['models']:
        print(json.dumps({key: value for key, value in model.items() if key != 'tracks'}))
    return 0 if report['reason'] == 'model_comparison_completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
