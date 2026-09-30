"""用多视角道路证据和同层边界长度共同恢复连续路面，保留原始观测概率。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import cv2
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import breadth_first_order, maximum_flow
from scipy.spatial import cKDTree

from dev_tools.minimap_reconstruct import terrain
from dev_tools.minimap_repair import evidence, small_components


def visible_terrain(image):
    """彩色图标和白色文字属于遮挡，不能当作地形空白反证。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    overlay = (((hue < 80) | (hue > 120)) & (saturation > 90) & (value > 150))
    overlay |= (saturation < 60) & (value > 185)
    overlay = cv2.dilate(overlay.astype(np.uint8), np.ones((3, 3), np.uint8))
    return (1 - overlay).astype(np.uint8)


def surface_cut(domain, probability, ratios, positive_core, forbidden, smoothness=3.):
    """最小割：观测误差与边界长度联合最小化，高度突变处降低同面约束。"""
    y, x = np.nonzero(domain)
    count = len(x)
    if not count:
        return np.zeros_like(domain)
    ids = np.full(domain.shape, -1, np.int32)
    ids[y, x] = np.arange(count)
    # 0.7 是道路／背景代价相同的位置；原始概率单独保存，不冒充空间推断概率。
    p = np.clip(probability[y, x], .02, .98)
    advantage = np.log(p / (1 - p)) - np.log(.7 / .3)
    foreground_cost, background_cost = np.maximum(-advantage, 0), np.maximum(advantage, 0)
    foreground_cost[forbidden[y, x]] = 10000
    background_cost[positive_core[y, x] & ~forbidden[y, x]] = 10000
    rows, cols, capacities = [], [], []
    for dy, dx in [(0, 1), (1, 0), (1, 1), (1, -1)]:
        height, width = ids.shape
        left = ids[:height - dy, max(0, -dx):min(width, width - dx)]
        right = ids[dy:, max(0, dx):min(width, width + dx)]
        valid = (left >= 0) & (right >= 0)
        a, b = left[valid], right[valid]
        delta = ratios[y[a], x[a]] - ratios[y[b], x[b]]
        weight = smoothness / np.hypot(dx, dy) * np.exp(-np.square(delta / .06))
        rows.extend([a, b])
        cols.extend([b, a])
        capacities.extend([weight, weight])
    source, sink = count, count + 1
    nodes = np.arange(count)
    rows.extend([np.full(count, source), nodes])
    cols.extend([nodes, np.full(count, sink)])
    capacities.extend([background_cost, foreground_cost])
    graph = coo_matrix((np.maximum(np.rint(np.concatenate(capacities) * 100), 0).astype(np.int32),
                        (np.concatenate(rows), np.concatenate(cols))), shape=(count + 2, count + 2)).tocsr()
    graph.eliminate_zeros()
    flow = maximum_flow(graph, source, sink)
    residual = graph - flow.flow
    residual.data[residual.data <= 0] = 0
    residual.eliminate_zeros()
    reachable = breadth_first_order(residual, source, directed=True, return_predecessors=False)
    foreground = reachable[reachable < count]
    result = np.zeros_like(domain)
    result[y[foreground], x[foreground]] = True
    return result


def restore(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Use a new output directory outside the baseline')
    meta = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    if meta.get('coordinate_model') != 'local_parallax':
        raise ValueError('Expected a local-parallax baseline')
    if meta.get('map_render_mode') == 'surface_road_mask':
        raise ValueError('Use the pixel-repaired baseline, not an already inferred surface mask')
    with np.load(source / 'surface_data.npz') as archive:
        data = {key: archive[key].copy() for key in archive.files}
    input_hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                    for name in ['map.png', 'map.json', 'surface_data.npz', 'reference.png']}
    for name, expected in meta['source_sha256'].items():
        if Path(name).name != name or hashlib.sha256((source / 'source' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Source frame hash mismatch')
    scan = json.loads((source / 'source/scan.json').read_text(encoding='utf-8'))
    images = [cv2.imread(str(source / 'source' / f['file'])) for f in scan['frames']]
    if not images or any(image is None for image in images):
        raise ValueError('Missing source frames')
    roads, visibility = [terrain(image) for image in images], [visible_terrain(image) for image in images]
    probability, ratios = data['terrain_probability'], data['parallax_ratio']
    shown = (probability > .5) & (data['frame_hits'] >= 3)
    confirmed = (probability > .85) & (data['frame_hits'] >= 3)
    core = cv2.erode(confirmed.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    core &= ~small_components(core, 512)
    domain = cv2.morphologyEx(shown.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((19, 19), np.uint8))
    domain = cv2.dilate(domain, np.ones((7, 7), np.uint8)) > 0
    domain |= small_components(~shown, 8192, enclosed=True)
    y, x = np.nonzero(domain)
    sy, sx = np.nonzero(confirmed & np.isfinite(ratios))
    if len(sx) < 32:
        raise ValueError('Insufficient reliable surface samples')
    distance, indices = cKDTree(np.column_stack([sx, sy])).query(np.column_stack([x, y]), k=32)
    samples = ratios[sy[indices], sx[indices]]
    proposals = np.percentile(samples, [10, 30, 50, 70, 90], axis=1).T.astype(np.float32)
    anchor = proposals[:, 2].copy()
    original = ratios[y, x]
    proposals = np.column_stack([np.where(np.isfinite(original), original, anchor), proposals])
    proposals[distance[:, -1] > 40] = np.nan
    valid = np.isfinite(proposals).all(axis=1)
    domain[y[~valid], x[~valid]] = False
    x, y, proposals, anchor = x[valid], y[valid], proposals[valid], anchor[valid]
    points = np.column_stack([x, y])
    training, validation = list(range(0, len(images), 2)), list(range(1, len(images), 2))
    chosen, best = proposals[:, 0].copy(), np.full(len(x), -np.inf)
    for index in range(proposals.shape[1]):
        print(f'Evaluating surface depth {index + 1}/{proposals.shape[1]} for {len(x)} pixels', flush=True)
        record = evidence(points, proposals[:, index], roads, meta, training, visibility)
        score = record['probability'] - .4 * np.abs(proposals[:, index] - anchor)
        select = (score > best) & ((index == 0) | ~core[y, x])
        chosen[select], best[select] = proposals[select, index], score[select]
    train = evidence(points, chosen, roads, meta, training, visibility)
    held = evidence(points, chosen, roads, meta, validation, visibility)
    all_frames = evidence(points, chosen, roads, meta, range(len(images)), visibility)
    pooled = np.zeros_like(probability)
    pooled[y, x] = np.minimum(train['probability'], held['probability'])
    depth = ratios.copy()
    depth[y, x] = chosen
    forbidden = np.ones_like(domain)
    supported = ((train['positive'] >= 3) & (held['positive'] >= 3)
                 & (train['baseline'] >= 60) & (held['baseline'] >= 60))
    # 成片补洞可接受边界附近的部分反证；明确空白和缺少跨视角支持仍禁止扩张。
    forbidden[y, x] = ~supported | (pooled[y, x] < .35)
    print('Optimizing connected surface boundaries', flush=True)
    mask = surface_cut(domain, pooled, depth, core, forbidden)
    data['surface_road_mask'] = mask
    data['surface_inferred_mask'] = mask & (pooled < .85)
    data['surface_validation_probability'] = pooled
    data['parallax_ratio'][y, x] = chosen
    data['terrain_probability'][y, x] = all_frames['probability']
    data['frame_hits'][y, x] = all_frames['positive']
    data['visible_frames'][y, x] = all_frames['seen']
    data['source_frame'][y, x] = all_frames['source_frame']
    data['source_roi'][y, x] = all_frames['source_roi']
    reference = cv2.imread(str(source / 'reference.png'))
    for frame in np.unique(all_frames['source_frame']):
        if frame < 0:
            continue
        selected = np.flatnonzero(all_frames['source_frame'] == frame)
        for start in range(0, len(selected), 16000):
            batch = selected[start:start + 16000]
            roi = all_frames['source_roi'][batch]
            colors = cv2.remap(images[int(frame)], roi[:, 0, None], roi[:, 1, None],
                               cv2.INTER_LINEAR)[:, 0]
            reference[y[batch], x[batch]] = colors
    map_image = np.full((*mask.shape, 3), (44, 35, 28), np.uint8)
    map_image[mask] = (186, 139, 59)
    output.mkdir(parents=True)
    for name in ['source', 'depth']:
        shutil.copytree(source / name, output / name)
    for name in ['tracks.json', 'surface_model.json']:
        shutil.copyfile(source / name, output / name)
    np.savez_compressed(output / 'surface_data.npz', **data)
    for name, image in [('map.png', map_image), ('reference.png', reference)]:
        if not cv2.imwrite(str(output / name), image):
            raise OSError(f'Could not write {name}')
    np.savez_compressed(output / 'surface_repair_evidence.npz', points=points, ratios=chosen, core=core,
                        domain=domain, forbidden=forbidden, original_shown=shown, original_confirmed=confirmed,
                        **{'training_' + name: value for name, value in train.items()},
                        **{'validation_' + name: value for name, value in held.items()})
    report = dict(input=str(source), input_sha256=input_hashes, raw_frames=len(images),
                  filled_background_pixels=int((mask & ~shown).sum()),
                  removed_pixels=int((shown & ~mask).sum()),
                  removed_confirmed_pixels=int((confirmed & ~mask).sum()),
                  inferred_pixels=int(data['surface_inferred_mask'].sum()),
                  road_pixels=int(mask.sum()), rendering='surface_road_mask',
                  training_frames=training, validation_frames=validation,
                  code_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                               for name in ['minimap_surface_repair.py', 'minimap_repair.py',
                                            'minimap_reconstruct.py']},
                  limitations=['Spatial surface inference is not an independent geometry or navigation validation.',
                               'Map renders surface_road_mask; terrain_probability retains raw observation ratios.',
                               'Parent per-frame depth remains unchanged; output depth uses local hypotheses.'])
    meta.update(surface_repair=report, navigation_ready=False, map_render_mode='surface_road_mask',
                image_sha256=hashlib.sha256((output / 'map.png').read_bytes()).hexdigest())
    (output / 'map.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    (output / 'surface_repair_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    for name, expected in input_hashes.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Baseline changed during repair')
    print(json.dumps({k: v for k, v in report.items() if not isinstance(v, (dict, list))}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    restore(args.source, args.output)
