"""从缓存帧重建具有局部视差的道路；保留多层观测，不推断跨层连通或场景点击。

python -m dev_tools.minimap_layered --source <scan-directory> --output <new-directory>
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial import Delaunay, cKDTree

from dev_tools.minimap_reconstruct import map_orientation, terrain, terrain_crop_bounds
from dev_tools.minimap_surface_fit import collect_tracks, fit_model, prepare_tracks


def project(matrix, points):
    """统一处理批量 ROI／平面点的齐次投影。"""
    points = np.asarray(points, float)
    return cv2.perspectiveTransform(points.reshape(1, -1, 2), np.asarray(matrix, float))[0].reshape(points.shape)


def surface_projection(matrix, coefficients, center, shape):
    """沿用网格的正方形与主点假设，恢复不同高度共同使用的垂直消失点。"""
    center = np.asarray(center)
    translation = np.array([[1, 0, -center[0]], [0, 1, -center[1]], [0, 0, 1.]])
    correction = np.linalg.inv(translation) @ np.array([[1, 0, 0], [0, 1, 0], [*coefficients, 1.]]) @ translation
    matrix = correction @ matrix
    inverse = np.linalg.inv(matrix)
    vanishing = inverse[:, :2] / inverse[2, :2]
    principal = np.array(shape[::-1]) / 2
    focal2 = -float((vanishing[:2, 0] - principal) @ (vanishing[:2, 1] - principal))
    if not np.isfinite(focal2) or focal2 < 900:
        raise ValueError('Surface projection has no valid metric camera')
    focal = np.sqrt(focal2)
    intrinsic = np.array([[focal, 0, principal[0]], [0, focal, principal[1]], [0, 0, 1.]])
    axes = np.linalg.inv(intrinsic) @ vanishing
    vertical = matrix @ intrinsic @ np.cross(axes[:, 0], axes[:, 1])
    if abs(vertical[2]) < 1e-6:
        raise ValueError('Vertical vanishing point is outside the finite surface chart')
    return matrix, vertical[:2] / vertical[2], float(focal)


def frame_support(tracks, model, matrix):
    """只让有实际匹配且重投影残差合格的观测支撑道路插值，保留全部验证统计。"""
    geometry = model['geometry']
    camera = np.asarray(geometry['camera'])
    origins = np.asarray(geometry['origins'])
    ratios = np.asarray(geometry['ratios'])
    support = [[] for _ in camera]
    rejected = 0
    for index, track in enumerate(tracks):
        frames = np.asarray(track['frames'])
        measured = project(matrix, track['screen'])
        predicted = origins[index] - ratios[index] * (camera[frames] - camera[frames[0]])
        errors = np.linalg.norm(predicted - measured, axis=1)
        for frame, point, error in zip(frames, track['screen'], errors):
            if error <= 3:
                support[frame].append([*point, ratios[index], float(error), index])
            else:
                rejected += 1
    return support, rejected


def ratio_field(shape, support):
    """仅在相近高度角点的局部三角形内插值；不跨大空洞或将高低道路平均。"""
    height, width = shape
    field = np.full(shape, np.nan, np.float32)
    confidence = np.zeros(shape, np.float32)
    if len(support) < 3:
        return field, confidence
    values = np.asarray(support)
    rounded = np.rint(values[:, :2]).astype(int)
    _, unique = np.unique(rounded, axis=0, return_index=True)
    values = values[unique]
    if len(values) < 3 or np.linalg.matrix_rank(values[:, :2] - values[0, :2]) < 2:
        return field, confidence
    mesh = Delaunay(values[:, :2])
    yy, xx = np.mgrid[:height, :width]
    pixels = np.column_stack([xx.ravel(), yy.ravel()])
    simplex = mesh.find_simplex(pixels)
    inside = simplex >= 0
    triangles = mesh.simplices[simplex[inside]]
    vertices = values[triangles]
    edges = vertices[:, :, :2] - np.roll(vertices[:, :, :2], 1, axis=1)
    valid = ((np.linalg.norm(edges, axis=2).max(axis=1) <= 180)
             & (np.ptp(vertices[:, :, 2], axis=1) <= .20))
    indices = np.flatnonzero(inside)[valid]
    selected = simplex[indices]
    barycentric = np.einsum('nij,nj->ni', mesh.transform[selected, :2],
                            pixels[indices] - mesh.transform[selected, 2])
    barycentric = np.column_stack([barycentric, 1 - barycentric.sum(axis=1)])
    ratios = values[mesh.simplices[selected], 2]
    field.ravel()[indices] = np.sum(barycentric * ratios, axis=1)
    distances = cKDTree(values[:, :2]).query(pixels[indices])[0]
    confidence.ravel()[indices] = np.maximum(.05, 1 - distances / 120)
    return field, confidence


def sweep_surface(index, images, camera, matrix, support):
    """用多视角平面扫描估计稠密视差；稀疏轨迹仅消除周期网格产生的等价匹配。"""
    shape = images[index].shape[:2]
    reference = images[index]
    displacement = np.linalg.norm(camera - camera[index], axis=1)
    candidates = [i for i in range(max(0, index - 5), min(len(images), index + 6))
                  if 35 < displacement[i] < 190]
    if len(candidates) < 2:
        return ratio_field(shape, support)
    candidates = sorted(candidates, key=lambda i: abs(displacement[i] - 95))[:4]
    inverse = np.linalg.inv(matrix)

    def features(image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
        texture = gray - cv2.GaussianBlur(gray, (31, 31), 5)
        return np.dstack([texture, terrain(image).astype(np.float32) / 255 * .3])

    first = features(reference)
    others = [features(images[i]) for i in candidates]
    valid = np.ones(shape, np.float32)
    valid[:15] = valid[-15:] = 0
    valid[:, :15] = valid[:, -15:] = 0
    valid[-50:, -90:] = 0
    ratios = np.arange(.76, 1.741, .02)
    costs = []
    for ratio in ratios:
        observations = []
        for other, frame in zip(others, candidates):
            delta = ratio * (camera[frame] - camera[index])
            transform = inverse @ np.array([[1, 0, delta[0]], [0, 1, delta[1]], [0, 0, 1.]]) @ matrix
            warped = cv2.warpPerspective(other, transform, shape[::-1])
            visible = cv2.warpPerspective(valid, transform, shape[::-1])
            error = np.abs(warped - first).sum(axis=2)
            error = cv2.boxFilter(error, -1, (9, 9))
            error[visible < .99] = 1
            observations.append(error)
        stacked = np.sort(observations, axis=0)
        costs.append((stacked[0] + stacked[1]) / 2)
    costs = np.asarray(costs)
    # 周期网格可出现多个光度极小值，附近可靠轮廓轨迹为其提供弱深度先验。
    if support:
        values = np.asarray(support)
        yy, xx = np.mgrid[:shape[0], :shape[1]]
        distance, nearest = cKDTree(values[:, :2]).query(np.column_stack([xx.ravel(), yy.ravel()]))
        prior = values[nearest, 2].reshape(shape)
        strength = np.exp(-distance.reshape(shape) / 45) * .02
        costs += np.minimum(np.abs(ratios[:, None, None] - prior[None]) / .12, 1) * strength
    best = np.argmin(costs, axis=0)
    yy, xx = np.indices(shape)
    minimum = costs[best, yy, xx]
    far = np.abs(np.arange(len(ratios))[:, None, None] - best[None]) >= 4
    alternative = np.min(np.where(far, costs, np.inf), axis=0)
    confidence = np.clip((alternative - minimum) / .025, 0, 1)
    field = ratios[best].astype(np.float32)
    accepted = (minimum < .045) & (confidence > .08) & (best > 0) & (best < len(ratios) - 1) & (valid > 0)
    field[~accepted] = np.nan
    confidence[~accepted] = 0
    return field, confidence


def regularize_surfaces(ratios, confidence, road, matrix):
    """以局部仿射视差平面解释稠密观测，剔除周期纹理误配，坡道保留非零斜率。"""
    shape = ratios.shape
    yy, xx = np.indices(shape)
    points = np.column_stack([xx.ravel(), yy.ravel()])
    plane = project(matrix, points).reshape(*shape, 2)
    center = np.mean(plane, axis=(0, 1))
    design = np.dstack([(plane - center) / 500, np.ones(shape)])
    available = (np.isfinite(ratios) & (confidence > .12) & (road > 0))
    selected = available & ((xx % 3) == 0) & ((yy % 3) == 0)
    samples = design[selected]
    values = ratios[selected]
    result = np.full(shape, np.nan, np.float32)
    weight = np.zeros(shape, np.float32)
    labels = np.full(shape, -1, np.int16)
    records = []
    rng = np.random.default_rng(40)
    for _ in range(8):
        if len(values) < 50:
            break
        picks = rng.integers(0, len(values), (350, 3))
        systems = samples[picks]
        nonsingular = np.abs(np.linalg.det(systems)) > .001
        if not nonsingular.any():
            break
        models = np.linalg.solve(systems[nonsingular], values[picks[nonsingular]])
        plausible = (np.linalg.norm(models[:, :2], axis=1) < 1.2)
        models = models[plausible]
        if not len(models):
            break
        errors = np.abs(samples @ models.T - values[:, None])
        best = int(np.argmax(np.sum(errors < .025, axis=0)))
        inliers = errors[:, best] < .025
        if inliers.sum() < max(50, len(values) * .1):
            break
        model = np.linalg.lstsq(samples[inliers], values[inliers], rcond=None)[0]
        prediction = design @ model
        measured = available & (np.abs(ratios - prediction) < .04)
        distance = cv2.distanceTransform((~measured).astype(np.uint8), cv2.DIST_L2, 3)
        neighborhood = cv2.dilate(road, np.ones((15, 15), np.uint8)) > 0
        accepted = (neighborhood & (distance <= 12) & (prediction > .76) & (prediction < 1.74)
                    & ((road == 0) | ~np.isfinite(ratios) | (np.abs(ratios - prediction) < .06)))
        score = np.where(measured, confidence, .12) * np.maximum(.05, 1 - distance / 14)
        choose = accepted & (score > weight)
        result[choose] = prediction[choose]
        weight[choose] = score[choose]
        labels[choose] = len(records)
        records.append({'ratio_plane': model.tolist(), 'center': center.tolist(), 'coordinate_scale': 500,
                        'sample_inliers': int(inliers.sum()), 'pixels': int(choose.sum())})
        keep = np.abs(samples @ model - values) >= .04
        samples, values = samples[keep], values[keep]
    return result, weight, labels, records


def surface_winners(best, x, y, candidate):
    """选择每个输出像素的最佳观测，让深度、来源和颜色使用同一组索引。"""
    # 比较值与存储值必须同精度，否则向上舍入会让最大值也无法认领像素。
    candidate = np.asarray(candidate, dtype=best.dtype)
    previous = best[y, x].copy()
    np.maximum.at(best, (y, x), candidate)
    selected = np.flatnonzero((candidate == best[y, x]) & (candidate > previous))
    # 四邻点投影可重复命中同一像素；并列时固定使用首个观测，避免重复索引写入。
    _, first = np.unique(y[selected] * best.shape[1] + x[selected], return_index=True)
    return selected[first]


def fusion_consistency(roads, camera, matrix, vertical, rotation, origin, ratios):
    """将候选三维道路重投影到全部原帧，用空白视野的反证排除错位和单帧尖刺。"""
    yy, xx = np.indices(ratios.shape, dtype=np.float32)
    world_x = (xx + origin[0]) * rotation[0, 0] + (yy + origin[1]) * rotation[1, 0]
    world_y = (xx + origin[0]) * rotation[0, 1] + (yy + origin[1]) * rotation[1, 1]
    supported = np.isfinite(ratios)
    ratios = np.nan_to_num(ratios, nan=1.)
    inverse = np.linalg.inv(matrix)
    positive = np.zeros(ratios.shape, np.uint16)
    seen = np.zeros(ratios.shape, np.uint16)
    for road, position in zip(roads, camera):
        qx = vertical[0] + ratios * (world_x - position[0] - vertical[0])
        qy = vertical[1] + ratios * (world_y - position[1] - vertical[1])
        denominator = inverse[2, 0] * qx + inverse[2, 1] * qy + inverse[2, 2]
        with np.errstate(divide='ignore', invalid='ignore'):
            x = (inverse[0, 0] * qx + inverse[0, 1] * qy + inverse[0, 2]) / denominator
            y = (inverse[1, 0] * qx + inverse[1, 1] * qy + inverse[1, 2]) / denominator
        height, width = road.shape
        valid = (supported & (denominator > 0) & (x >= 15) & (x < width - 15)
                 & (y >= 15) & (y < height - 15) & ~((x > width - 90) & (y > height - 50)))
        mapped = cv2.remap(road, np.where(valid, x, -1).astype(np.float32),
                           np.where(valid, y, -1).astype(np.float32), cv2.INTER_LINEAR)
        seen += valid.astype(np.uint16)
        positive += (valid & (mapped > 127)).astype(np.uint16)
    probability = np.divide(positive, seen, out=np.zeros(ratios.shape, np.float32), where=seen > 0)
    return probability, positive, seen


def reconstruct(source, output, depth_cache=None, stop_file=None):
    """在新目录导出道路、局部高度和反查坐标；原始扫描及其他地图包保持不变。"""
    def check_stop():
        if stop_file is not None and Path(stop_file).exists():
            raise KeyboardInterrupt('Stopped during layered reconstruction.')

    check_stop()
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Use a new output directory outside the source scan')
    scan_raw = (source / 'scan.json').read_bytes()
    data = json.loads(scan_raw)
    if data.get('version') != 2 or data.get('status') not in ('roads_exhausted', 'raster_closed'):
        raise ValueError('Expected a completed version 2 scan')
    hashes = {'scan.json': hashlib.sha256(scan_raw).hexdigest()}
    code_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (Path(__file__), Path(__file__).with_name('minimap_surface_fit.py'))}
    images = []
    for frame in data['frames']:
        if Path(frame['file']).is_absolute() or '..' in Path(frame['file']).parts:
            raise ValueError('Expected a relative frame path within the source scan')
        path = (source / frame['file']).resolve()
        if not path.is_relative_to(source):
            raise ValueError('Frame path leaves source directory')
        raw = path.read_bytes()
        hashes[frame['file']] = hashlib.sha256(raw).hexdigest()
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f'Cannot decode {path}')
        images.append(image)
    if not images:
        raise ValueError('Scan has no frames')
    shape = images[0].shape[:2]
    if any(image.shape[:2] != shape for image in images):
        raise ValueError('Frame dimensions differ')
    output.mkdir(parents=True)
    depth_root = output / 'depth'
    depth_root.mkdir()
    camera = np.asarray([frame['position'] for frame in data['frames']], float)
    print(f'Collecting local road tracks from {len(images)} cached frames', flush=True)
    tracks = prepare_tracks(camera, collect_tracks(source, data, step=1, corners=400, distance=6), data['cell_px'])
    check_stop()
    if len(tracks) < 20:
        raise ValueError('Insufficient local surface tracks')
    model = fit_model(camera, tracks, data['cell_px'], np.asarray(data['warp_size']) / 2, True,
                      return_geometry=True)
    check_stop()
    if not model['converged']:
        raise ValueError('Local surface optimization did not converge')
    print(json.dumps(model['validation']), flush=True)
    matrix, vertical, focal = surface_projection(np.asarray(data['matrix']), model['perspective_coefficients'],
                                                 model['geometry']['center'], shape)
    camera = np.asarray(model['geometry']['camera'])
    cached_hashes = {}
    if depth_cache is not None:
        depth_cache = Path(depth_cache).resolve()
        cached_report = json.loads((depth_cache / 'map.json').read_text(encoding='utf-8'))
        if (cached_report['source_sha256'] != hashes
                or not np.allclose(cached_report['projection'], matrix, atol=1e-10, rtol=0)
                or not np.allclose(cached_report['camera'], camera, atol=1e-8, rtol=0)):
            raise ValueError('Depth cache does not match source frames and fitted camera')
    support, rejected = frame_support(tracks, model, matrix)
    orientation, _ = map_orientation(matrix, data['warp_size'], shape, 'screen_oblique')
    rotation = orientation[:2, :2]
    batches, frame_reports = [], []
    for index, image in enumerate(images):
        check_stop()
        if depth_cache is None:
            ratios, weights = sweep_surface(index, images, camera, matrix, support[index])
        else:
            cache_path = depth_cache / 'depth' / f'frame_{index:05d}.npz'
            cached_hashes[cache_path.name] = hashlib.sha256(cache_path.read_bytes()).hexdigest()
            with np.load(cache_path, allow_pickle=False) as saved:
                ratios, weights = saved['raw_ratio'], saved['raw_confidence']
            if (ratios.shape != shape or weights.shape != shape or not np.isfinite(weights).all()
                    or np.any((weights < 0) | (weights > 1))):
                raise ValueError('Invalid cached depth dimensions or confidence')
        if index % 20 == 0:
            print(f'Reconstructing frame {index}/{len(images)}', flush=True)
        road = terrain(image)
        raw_ratios, raw_weights = ratios, weights
        ratios, weights, labels, planes = regularize_surfaces(ratios, weights, road, matrix)
        np.savez_compressed(depth_root / f'frame_{index:05d}.npz', ratio=ratios, confidence=weights,
                            local_surface=labels, raw_ratio=raw_ratios, raw_confidence=raw_weights)
        valid = np.ones(shape, np.uint8)
        valid[:15] = valid[-15:] = 0
        valid[:, :15] = valid[:, -15:] = 0
        valid[-50:, -90:] = 0
        region = cv2.dilate(road, np.ones((21, 21), np.uint8)) > 0
        accepted = np.isfinite(ratios) & (valid > 0) & region
        road_count = int(np.count_nonzero((road > 0) & (valid > 0)))
        supported = int(np.count_nonzero(accepted & (road > 0)))
        frame_reports.append({'frame': index, 'road_pixels': road_count, 'supported_road_pixels': supported,
                              'support_points': len(support[index]), 'local_planes': planes})
        if not accepted.any():
            continue
        # 双倍采样后按四邻点加权投票，避免透视放大在输出栅格上留下点状空洞。
        dense_valid = cv2.resize(accepted.astype(np.uint8), None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST) > 0
        y, x = np.nonzero(dense_valid)
        roi = np.column_stack([(x + .5) / 2 - .5, (y + .5) / 2 - .5]).astype(np.float32)
        source_y, source_x = y // 2, x // 2
        ratio = ratios[source_y, source_x]
        point = vertical + (project(matrix, roi) - vertical) / ratio[:, None] + camera[index]
        point = point @ rotation.T
        batches.append((index, point.astype(np.float32), roi, ratio,
                        weights[source_y, source_x], road[source_y, source_x] / 255.,
                        image[source_y, source_x]))
    if not batches:
        raise ValueError('No roads have local surface support')
    low = np.floor(np.min([batch[1].min(axis=0) for batch in batches], axis=0)).astype(int) - 48
    high = np.ceil(np.max([batch[1].max(axis=0) for batch in batches], axis=0)).astype(int) + 49
    size = high - low
    if np.any(size > 16000) or np.prod(size) > 20_000_000:
        raise ValueError(f'Implausible surface canvas: {size}')
    canvas_shape = tuple(size[::-1])
    coverage = np.zeros(canvas_shape, np.float32)
    roads = np.zeros_like(coverage)
    frame_hits = np.zeros(canvas_shape, np.uint16)
    best = np.zeros_like(coverage)
    reference = np.zeros((*canvas_shape, 3), np.uint8)
    source_frame = np.full(canvas_shape, -1, np.int16)
    source_roi = np.full((*canvas_shape, 2), np.nan, np.float32)
    parallax = np.full(canvas_shape, np.nan, np.float32)
    for index, point, roi, ratio, weight, road, colors in batches:
        frame_road = np.zeros_like(coverage)
        point = point - low
        base = np.floor(point).astype(int)
        fraction = point - base
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            x, y = (base + [dx, dy]).T
            w = weight * (fraction[:, 0] if dx else 1 - fraction[:, 0])
            w *= fraction[:, 1] if dy else 1 - fraction[:, 1]
            np.add.at(coverage, (y, x), w)
            np.add.at(roads, (y, x), w * road)
            np.add.at(frame_road, (y, x), w * road)
            candidate = w * (road > .5)
            choose = surface_winners(best, x, y, candidate)
            xx, yy = x[choose], y[choose]
            reference[yy, xx] = colors[choose]
            source_frame[yy, xx] = index
            source_roi[yy, xx] = roi[choose]
            parallax[yy, xx] = ratio[choose]
        frame_hits += (frame_road > .15).astype(np.uint16)
    probability = np.divide(roads, coverage, out=np.zeros_like(roads), where=coverage > 0)
    bounds = terrain_crop_bounds(probability)
    left, top, right, bottom = bounds['box']
    crop = np.s_[top:bottom, left:right]
    low += [left, top]
    probability, coverage = probability[crop], coverage[crop]
    frame_hits = frame_hits[crop]
    source_frame, source_roi, parallax = source_frame[crop], source_roi[crop], parallax[crop]
    reference = reference[crop]
    print('Checking fused surfaces against every source frame', flush=True)
    raw_probability = probability
    probability, frame_hits, visible_frames = fusion_consistency(
        [terrain(image) for image in images], camera, matrix, vertical, rotation, low, parallax)
    map_image = np.full((*probability.shape, 3), (44, 35, 28), np.uint8)
    confirmed = (probability > .85) & (frame_hits >= 3)
    uncertain = (probability > .5) & ~confirmed & (frame_hits >= 3)
    map_image[confirmed] = (186, 139, 59)
    map_image[uncertain] = (104, 86, 66)
    report = {'schema_version': 2, 'chapter': data.get('chapter'), 'difficulty': 'normal',
              'status': 'needs_geometry_review', 'navigation_ready': False,
              'orientation': 'screen_oblique', 'depth_directory': 'depth', 'raw_frames': 'source',
              'depth_model': 'Per-frame continuous parallax ratio; height/camera_height = 1 - 1/ratio. '
                             'These are relative estimates, not physical heights or connectivity labels.',
              'whole_camera_domain_verified': False, 'source': str(source), 'source_sha256': hashes,
              'image': 'map.png', 'reference_image': 'reference.png', 'size': list(probability.shape[::-1]),
              'coordinates': {'unit': 'rectified_grid_pixel', 'origin': 'top_left', 'x': 'right', 'y': 'down'},
              'coordinate_model': 'local_parallax', 'frames': len(images), 'tracks': len(tracks),
              'supported_observation_fraction': sum(item['supported_road_pixels'] for item in frame_reports)
                                                / max(1, sum(item['road_pixels'] for item in frame_reports)),
              'display_evidence': 'All-frame reprojection. Blue: probability > 0.85 and at least 3 source frames; '
                                  'gray: probability > 0.5 and at least 3 frames. Raw evidence is retained in NPZ.',
              'validation': model['validation'], 'rejected_observations': rejected,
              'projection': matrix.tolist(), 'vertical_origin': vertical.tolist(), 'focal_px': focal,
              'rotation': rotation.tolist(), 'origin': low.tolist(), 'camera': camera.tolist(), 'crop': bounds,
              'frame_to_map': '(vertical + (project(projection, roi) - vertical) / ratio + camera[frame]) '
                              '@ rotation.T - origin',
              'frame_support': frame_reports, 'connections': [],
              'limitations': ['Local scale interpolation requires observed road support.',
                              'Camera calibration assumes square grid and ROI-centered principal point.',
                              'Projected road crossings do not establish connectivity.',
                              'No independent global geometry or field-click validation.']}
    if any(hashlib.sha256((source / name).read_bytes()).hexdigest() != digest for name, digest in hashes.items()):
        raise RuntimeError('Source scan changed during reconstruction')
    snapshot = output / 'source'
    snapshot.mkdir()
    for name, digest in hashes.items():
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Source snapshot hash mismatch')
    cv2.imwrite(str(output / 'map.png'), map_image)
    cv2.imwrite(str(output / 'reference.png'), reference)
    report['image_sha256'] = hashlib.sha256((output / 'map.png').read_bytes()).hexdigest()
    report['code_sha256'] = code_hashes
    report['depth_cache'] = None if depth_cache is None else str(depth_cache)
    report['depth_cache_sha256'] = cached_hashes
    report['runtime'] = {'numpy': np.__version__, 'opencv': cv2.__version__}
    (output / 'map.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (output / 'surface_model.json').write_text(json.dumps(model, indent=2), encoding='utf-8')
    (output / 'tracks.json').write_text(json.dumps(tracks), encoding='utf-8')
    np.savez_compressed(output / 'surface_data.npz', terrain_probability=probability, coverage=coverage,
                        frame_hits=frame_hits, visible_frames=visible_frames, raw_probability=raw_probability,
                        source_frame=source_frame, source_roi=source_roi, parallax_ratio=parallax,
                        projection=matrix, camera=camera, rotation=rotation, vertical_origin=vertical, origin=low)
    print(json.dumps({'output': str(output), 'size': report['size'], 'frames': len(images),
                      'tracks': len(tracks), 'navigation_ready': False}), flush=True)
    return report


def main():
    """仅消费完成的本机扫描，不连接游戏或改写旧包。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--depth-cache', type=Path, help='Reuse raw depth from a reconstruction with identical inputs.')
    args = parser.parse_args()
    cv2.setNumThreads(2)
    reconstruct(args.source, args.output, args.depth_cache)


if __name__ == '__main__':
    main()
