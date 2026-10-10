"""在既有分层坐标内，以原始帧复核局部空洞与孤立碎片，不重新拟合整图。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import cv2
import numpy as np
from scipy.spatial import cKDTree

from dev_tools.minimap_layered import project
from dev_tools.minimap_reconstruct import terrain


def small_components(mask, maximum, enclosed=False):
    """仅提出局部区域；触及画布边界的背景不能当作道路内部空洞。"""
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = (stats[:, cv2.CC_STAT_AREA] <= maximum)
    keep[0] = False
    if enclosed:
        keep[np.unique(np.r_[labels[0], labels[-1], labels[:, 0], labels[:, -1]])] = False
    return keep[labels]


def depth_proposals(points, seed_mask, ratios):
    """从邻近可信道路提出高度候选，拒绝混合高度边界和远距离外推。"""
    y, x = np.nonzero(seed_mask & np.isfinite(ratios))
    if len(x) < 16 or not len(points):
        return np.full((len(points), 3), np.nan, np.float32)
    distance, indices = cKDTree(np.column_stack([x, y])).query(points, k=16)
    values = ratios[y[indices], x[indices]]
    low, median, high = np.percentile(values, [10, 50, 90], axis=1)
    acceptable = (distance[:, -1] <= 18) & (high - low <= .06)
    result = np.column_stack([median, low, high]).astype(np.float32)
    result[~acceptable] = np.nan
    return result


def evidence(points, ratios, roads, meta, frames, visibility=None):
    """逐帧回投候选，同时统计空白反证、道路内部支持和正观测的相机基线。"""
    points, ratios = np.asarray(points, float), np.asarray(ratios, np.float32)
    total = len(points)
    positive, seen, interior = [np.zeros(total, np.uint16) for _ in range(3)]
    best = np.full(total, -1., np.float32)
    owner = np.full(total, -1, np.int16)
    roi = np.full((total, 2), np.nan, np.float32)
    minimum, maximum = np.full((total, 2), np.inf), np.full((total, 2), -np.inf)
    if not total:
        return dict(probability=best.copy(), positive=positive, seen=seen, interior=interior,
                    baseline=np.zeros(0), source_frame=owner, source_roi=roi)
    world = (points + meta['origin']) @ np.asarray(meta['rotation'])
    inverse = np.linalg.inv(meta['projection'])
    vertical, cameras = np.asarray(meta['vertical_origin']), np.asarray(meta['camera'])
    for frame in frames:
        road = roads[frame]
        q = vertical + ratios[:, None] * (world - cameras[frame] - vertical)
        denominator = q @ inverse[2, :2] + inverse[2, 2]
        image = project(inverse, q)
        x, y = image.T
        height, width = road.shape
        valid = (np.isfinite(ratios) & (denominator > 0) & (x >= 15) & (x < width - 15)
                 & (y >= 15) & (y < height - 15) & ~((x > width - 90) & (y > height - 50)))
        sample, margin = np.zeros(total), np.zeros(total)
        distance = cv2.distanceTransform((road > 0).astype(np.uint8), cv2.DIST_L2, 5)
        for start in range(0, total, 16000):
            end = min(total, start + 16000)
            mx = np.where(valid[start:end], x[start:end], -1).astype(np.float32)[None]
            my = np.where(valid[start:end], y[start:end], -1).astype(np.float32)[None]
            sample[start:end] = cv2.remap(road, mx, my, cv2.INTER_LINEAR)[0]
            margin[start:end] = cv2.remap(distance, mx, my, cv2.INTER_LINEAR)[0]
            if visibility is not None:
                clear = cv2.remap(visibility[frame], mx, my, cv2.INTER_NEAREST)[0] > 0
                valid[start:end] &= clear
        yes = valid & (sample > 127)
        seen += valid
        positive += yes
        interior += yes & (margin >= 2)
        minimum[yes] = np.minimum(minimum[yes], cameras[frame])
        maximum[yes] = np.maximum(maximum[yes], cameras[frame])
        score = np.minimum.reduce([x, y, width - x, height - y])
        choose = yes & (score > best)
        best[choose], owner[choose], roi[choose] = score[choose], frame, image[choose]
    probability = np.divide(positive, seen, out=np.zeros(total, np.float32), where=seen > 0)
    span = np.zeros(total)
    observed = positive > 0
    span[observed] = np.linalg.norm(maximum[observed] - minimum[observed], axis=1)
    return dict(probability=probability, positive=positive, seen=seen, interior=interior,
                baseline=span, source_frame=owner, source_roi=roi)


def qualified(record, probability=.85):
    """相邻重复帧不替代运动基线；边缘擦过也不足以证明整个补点属于道路。"""
    return ((record['probability'] > probability) & (record['positive'] >= 3)
            & (record['interior'] >= 3) & (record['baseline'] >= 80))


def repair(source, output):
    """复制基线到新目录，修订道路数据并保存每个修改像素的训练／复核证据。"""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Use a new output directory outside the baseline')
    meta = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    if meta.get('coordinate_model') != 'local_parallax':
        raise ValueError('Expected the layered local-parallax baseline')
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
              for name in ['map.json', 'map.png', 'reference.png', 'surface_data.npz']}
    with np.load(source / 'surface_data.npz', allow_pickle=False) as saved:
        data = {name: saved[name].copy() for name in saved.files}
    scan = json.loads((source / 'source/scan.json').read_text(encoding='utf-8'))
    images = []
    for name, expected in meta['source_sha256'].items():
        if Path(name).name != name or hashlib.sha256((source / 'source' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Baseline scan hash mismatch')
    for index, frame in enumerate(scan['frames']):
        if frame['id'] != index:
            raise ValueError('Expected sequential source frame IDs')
        image = cv2.imread(str(source / 'source' / frame['file']))
        if image is None:
            raise ValueError('Unreadable source frame')
        images.append(image)
    roads = [terrain(image) for image in images]
    training, validation = list(range(0, len(images), 2)), list(range(1, len(images), 2))
    probability, hits, ratios = data['terrain_probability'], data['frame_hits'], data['parallax_ratio']
    shown = (probability > .5) & (hits >= 3)
    confirmed = (probability > .85) & (hits >= 3)
    seeds = (probability > .90) & (hits >= 5)
    holes = small_components(~shown, 2048, enclosed=True)
    holes |= small_components(~confirmed, 1024, enclosed=True)
    closed = cv2.morphologyEx(shown.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)) > 0
    candidates = (holes | (closed & ~shown)) & ~confirmed
    y, x = np.nonzero(candidates)
    points = np.column_stack([x, y])
    proposals = depth_proposals(points, seeds, ratios)
    supported = np.isfinite(proposals).all(axis=1)
    points, proposals = points[supported], proposals[supported]
    print(f'Checking {len(points)} local repair candidates against original views', flush=True)
    chosen = np.full(len(points), np.nan, np.float32)
    best = np.full(len(points), -np.inf)
    for index in range(proposals.shape[1]):
        record = evidence(points, proposals[:, index], roads, meta, training)
        score = record['probability'] - np.abs(proposals[:, index] - proposals[:, 0]) * .05
        select = qualified(record, .9) & (score > best)
        chosen[select], best[select] = proposals[select, index], score[select]
    proposed = np.isfinite(chosen)
    points, chosen = points[proposed], chosen[proposed]
    train = evidence(points, chosen, roads, meta, training)
    held = evidence(points, chosen, roads, meta, validation)
    accepted = qualified(train, .9) & qualified(held, .85)
    repair_points, repair_ratios = points[accepted], chosen[accepted]
    all_frames = evidence(repair_points, repair_ratios, roads, meta, range(len(images)))
    added = np.zeros_like(shown)
    if len(repair_points):
        x, y = repair_points.T
        added[y, x] = True
        probability[y, x] = all_frames['probability']
        hits[y, x] = all_frames['positive']
        data['visible_frames'][y, x] = all_frames['seen']
        ratios[y, x] = repair_ratios
        data['source_frame'][y, x] = all_frames['source_frame']
        data['source_roi'][y, x] = all_frames['source_roi']
    fragments = small_components(shown, 256)
    fy, fx = np.nonzero(fragments)
    fragment_points = np.column_stack([fx, fy])
    first = evidence(fragment_points, ratios[fy, fx], roads, meta, training)
    second = evidence(fragment_points, ratios[fy, fx], roads, meta, validation)
    strong = qualified(first) & qualified(second)
    # 只隐藏整块均无充分证据的孤岛；保留具有可靠核心的真实小路面及其边缘。
    _, labels = cv2.connectedComponents(fragments.astype(np.uint8), connectivity=8)
    protected = np.unique(labels[fy[strong], fx[strong]])
    suppressed = fragments & ~np.isin(labels, protected) & ~added
    probability[suppressed] = 0
    confirmed_after = (probability > .85) & (hits >= 3)
    uncertain_after = (probability > .5) & ~confirmed_after & (hits >= 3)
    map_image = np.full((*shown.shape, 3), (44, 35, 28), np.uint8)
    map_image[confirmed_after], map_image[uncertain_after] = (186, 139, 59), (104, 86, 66)
    reference = cv2.imread(str(source / 'reference.png'))
    for frame in np.unique(all_frames['source_frame']):
        selected = all_frames['source_frame'] == frame
        roi = all_frames['source_roi'][selected]
        x, y = repair_points[selected].T
        for start in range(0, len(roi), 16000):
            end = start + 16000
            color = cv2.remap(images[int(frame)], roi[start:end, 0, None], roi[start:end, 1, None],
                              cv2.INTER_LINEAR)
            reference[y[start:end], x[start:end]] = color[:, 0]
    output.mkdir(parents=True)
    for folder in ['source', 'depth']:
        shutil.copytree(source / folder, output / folder)
    for name in ['surface_model.json', 'tracks.json']:
        shutil.copyfile(source / name, output / name)
    np.savez_compressed(output / 'surface_data.npz', **data)
    for name, image in [('map.png', map_image), ('reference.png', reference)]:
        if not cv2.imwrite(str(output / name), image):
            raise OSError(f'Could not write {name}')
    np.savez_compressed(output / 'repair_evidence.npz', proposed_points=points, proposed_ratios=chosen,
                        accepted=accepted, added=added, suppressed=suppressed, original_shown=shown,
                        original_confirmed=confirmed,
                        **{'training_' + name: value for name, value in train.items()},
                        **{'validation_' + name: value for name, value in held.items()})
    audit = dict(input=str(source), input_sha256=hashes, raw_frames=len(images), training_frames=training,
                 validation_frames=validation, proposed_pixels=len(points), repaired_pixels=int(added.sum()),
                 filled_background_pixels=int((added & ~shown).sum()),
                 promoted_uncertain_pixels=int((added & shown).sum()),
                 suppressed_fragment_pixels=int(suppressed.sum()),
                 preserved_fragment_pixels=int((fragments & ~suppressed).sum()),
                 same_projection_camera_and_canvas=True,
                 limitations=['Frame split validates local repairs, not independent global geometry.',
                              'Depth proposals use neighboring reliable roads; mixed-height boundaries are not filled.',
                              'Suppressed isolated fragments lack sufficient support, not proven nonexistent.',
                              'Original per-frame depth estimates remain unchanged; repaired output depth is inferred.',
                              'No navigation, layer-connection or field-click validation.'])
    meta.update(image_sha256=hashlib.sha256((output / 'map.png').read_bytes()).hexdigest(), repair=audit,
                navigation_ready=False)
    meta['repair']['code_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (output / 'map.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    (output / 'repair_report.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    for name, expected in hashes.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Baseline changed during repair')
    print(json.dumps({key: value for key, value in audit.items() if key not in
                      ['input_sha256', 'training_frames', 'validation_frames', 'limitations']}), flush=True)
    return audit


def main():
    """对指定分层基线进行离线局部修复，不改写旧包或发送游戏输入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    repair(args.source, args.output)


if __name__ == '__main__':
    main()
