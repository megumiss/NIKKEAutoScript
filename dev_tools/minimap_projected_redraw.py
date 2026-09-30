"""把原始帧的道路与空白投影回既有 map；仅重绘获得跨帧一致支持的局部块。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from collections import Counter

import cv2
import numpy as np

from dev_tools.minimap_layered import project
from dev_tools.minimap_reconstruct import terrain
from dev_tools.minimap_surface_repair import visible_terrain
from module.campaign_prototype.local_projection import frame_plane_to_map


def warp_patch(image, matrix, rect, interpolation=cv2.INTER_NEAREST):
    left, top, right, bottom = rect
    shift = np.array([[1, 0, -left], [0, 1, -top], [0, 0, 1.]])
    return cv2.warpPerspective(image, shift @ matrix, (right - left, bottom - top), flags=interpolation)


def redraw(source, output, regions=False, stop_file=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Use a new output directory outside the baseline')
    meta = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    if meta.get('coordinate_model') != 'local_parallax' or meta.get('map_render_mode'):
        raise ValueError('Expected the original local-parallax baseline')
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
              for name in ['map.png', 'reference.png', 'map.json', 'surface_data.npz']}
    for name, expected in meta['source_sha256'].items():
        if Path(name).name != name or hashlib.sha256((source / 'source' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Source hash mismatch')
    scan = json.loads((source / 'source/scan.json').read_text(encoding='utf-8'))
    images = [cv2.imread(str(source / 'source' / item['file'])) for item in scan['frames']]
    if any(image is None for image in images):
        raise ValueError('Missing source image')
    roads = [terrain(image) for image in images]
    clear = [visible_terrain(image) for image in images]
    caches = []
    for frame in range(len(images)):
        with np.load(source / 'depth' / f'frame_{frame:05d}.npz') as data:
            caches.append({name: data[name].copy() for name in ['ratio', 'local_surface']})
        clear[frame][:15] = clear[frame][-15:] = 0
        clear[frame][:, :15] = clear[frame][:, -15:] = 0
        clear[frame][-50:, -90:] = 0
    with np.load(source / 'surface_data.npz') as saved:
        baseline = {name: saved[name].copy() for name in saved.files}
    if regions:
        from dev_tools.minimap_region_redraw import redraw_regions
        return redraw_regions(source, output, meta, hashes, images, roads, clear, caches, baseline, stop_file)
    rejected = Counter()
    probability, hits = baseline['terrain_probability'], baseline['frame_hits']
    anchors = (probability > .9) & (hits >= 5)
    height, width = probability.shape
    before = cv2.imread(str(source / 'map.png'))
    result, reference = before.copy(), cv2.imread(str(source / 'reference.png'))
    patch_ids = np.full((height, width), -1, np.int16)
    source_roi = np.full((height, width, 2), np.nan, np.float32)
    owner = np.full((height, width), -1, np.int16)
    records, attempted = [], 0
    vertical = np.asarray(meta['vertical_origin'])
    rotation, origin = np.asarray(meta['rotation']), np.asarray(meta['origin'])
    inverse = np.linalg.inv(meta['projection'])
    matrices = [[frame_plane_to_map(meta, f, plane) for plane in entry['local_planes']]
                for f, entry in enumerate(meta['frame_support'])]
    for top in range(0, height, 64):
        if stop_file is not None and Path(stop_file).exists():
            raise KeyboardInterrupt('Stopped during raw-frame redraw.')
        print(f'Projecting original views: row {top}/{height}', flush=True)
        for left in range(0, width, 64):
            rect = (left, top, min(width, left + 64), min(height, top + 64))
            area = np.s_[top:rect[3], left:rect[2]]
            yy, xx = np.nonzero(anchors[area])
            if len(xx) < 100:
                rejected['tiles_without_100_anchors'] += 1
                continue
            attempted += 1
            xx, yy = xx[::4] + left, yy[::4] + top
            points = np.column_stack([xx, yy])
            ratios = baseline['parallax_ratio'][yy, xx]
            owners, counts = np.unique(baseline['source_frame'][yy, xx], return_counts=True)
            frames = set()
            for frame in owners[np.argsort(counts)[-6:]]:
                frames.update(int(i) for i in [frame - 1, frame, frame + 1] if 0 <= i < len(images))
            world = (points + origin) @ rotation
            candidates = []
            for frame in sorted(frames):
                q = vertical + ratios[:, None] * (world - meta['camera'][frame] - vertical)
                roi = project(inverse, q)
                px, py = np.rint(roi).astype(int).T
                sh, sw = roads[frame].shape
                valid = (px >= 15) & (px < sw - 15) & (py >= 15) & (py < sh - 15)
                if valid.mean() < .85:
                    rejected['frame_anchor_visibility'] += 1
                    continue
                labels = caches[frame]['local_surface'][py[valid], px[valid]]
                labels, votes = np.unique(labels[labels >= 0], return_counts=True)
                for label in labels[np.argsort(votes)[-2:]]:
                    matrix = matrices[frame][label]
                    error = np.linalg.norm(project(matrix, roi[valid]) - points[valid], axis=1)
                    p90 = float(np.percentile(error, 90))
                    if np.median(error) > 2 or p90 > 5:
                        rejected['plane_disagrees_with_old_map'] += 1
                        continue
                    # 来源帧里另一高度的已知路面不可被这张局部单应当作同层绘制。
                    plane = meta['frame_support'][frame]['local_planes'][int(label)]
                    sy, sx = np.indices(roads[frame].shape)
                    q = project(meta['projection'], np.column_stack([sx.ravel(), sy.ravel()]))
                    design = np.column_stack([(q - plane['center']) / plane['coordinate_scale'], np.ones(len(q))])
                    prediction = (design @ plane['ratio_plane']).reshape(roads[frame].shape)
                    known = caches[frame]['ratio']
                    compatible = ~np.isfinite(known) | (np.abs(known - prediction) < .06) | (roads[frame] == 0)
                    valid_image = clear[frame] * compatible.astype(np.uint8)
                    visible = warp_patch(valid_image, matrix, rect) > 0
                    road = warp_patch(roads[frame], matrix, rect) > 0
                    if visible.mean() < .85:
                        rejected['plane_visibility'] += 1
                        continue
                    anchor_mask = anchors[area] & visible
                    recall = float(road[anchor_mask].mean()) if anchor_mask.any() else 0
                    if recall < .95:
                        rejected['plane_old_map_road_recall'] += 1
                        continue
                    candidates.append(dict(frame=frame, label=int(label), matrix=matrix, visible=visible,
                                           road=road, score=recall + visible.mean() * .2 - p90 * .02,
                                           p90=p90))
            candidates.sort(key=lambda entry: entry['score'], reverse=True)
            chosen = None
            for first in candidates:
                for second in candidates:
                    baseline_distance = np.linalg.norm(np.asarray(meta['camera'][first['frame']])
                                                       - meta['camera'][second['frame']])
                    if baseline_distance < 60:
                        continue
                    common = first['visible'] & second['visible']
                    union = common & (first['road'] | second['road'])
                    if common.mean() < .8 or union.sum() < 100:
                        continue
                    iou = np.count_nonzero(common & first['road'] & second['road']) / union.sum()
                    if iou >= .9:
                        chosen = (first, second, float(iou))
                        break
                if chosen is not None:
                    break
            if chosen is None:
                rejected['tiles_without_two_frame_agreement'] += 1
                continue
            first, second, iou = chosen
            valid = first['visible'] & second['visible']
            road = first['road']
            frame, matrix = first['frame'], first['matrix']
            patch = np.full((*road.shape, 3), (44, 35, 28), np.uint8)
            patch[road] = (186, 139, 59)
            result[area][valid] = patch[valid]
            reference[area][valid] = warp_patch(images[frame], matrix, rect, cv2.INTER_LINEAR)[valid]
            py, px = np.indices(road.shape)
            mapped = project(np.linalg.inv(matrix), np.column_stack([px.ravel() + left, py.ravel() + top]))
            source_roi[area][valid] = mapped.reshape(*road.shape, 2)[valid]
            owner[area][valid], patch_ids[area][valid] = frame, len(records)
            records.append(dict(id=len(records), rect=list(rect), source_frame=frame, local_surface=first['label'],
                                roi_to_map=matrix.tolist(), check_frame=second['frame'],
                                check_roi_to_map=second['matrix'].tolist(), road_iou=iou,
                                anchor_p90_px=first['p90'], painted_pixels=int(valid.sum())))
    output.mkdir(parents=True)
    for folder in ['source', 'depth']:
        shutil.copytree(source / folder, output / folder)
    for name in ['surface_data.npz', 'tracks.json', 'surface_model.json']:
        shutil.copyfile(source / name, output / name)
    for name, image in [('map.png', result), ('reference.png', reference)]:
        if not cv2.imwrite(str(output / name), image):
            raise OSError(f'Could not write {name}')
    np.savez_compressed(output / 'redraw_data.npz', patch_id=patch_ids, source_frame=owner, source_roi=source_roi,
                        road_mask=np.all(result == (186, 139, 59), axis=2))
    changed = np.any(before != result, axis=2)
    dependencies = [Path(__file__), Path('module/campaign_prototype/local_projection.py'),
                    Path('dev_tools/minimap_reconstruct.py'), Path('dev_tools/minimap_surface_repair.py')]
    audit = dict(input=str(source), input_sha256=hashes, attempted_patches=attempted,
                 painted_patches=len(records), painted_pixels=int((patch_ids >= 0).sum()),
                 changed_pixels=int(changed.sum()), patches=records,
                 rejection_events=dict(rejected),
                 code_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in dependencies},
                 limitations=['Only validated local patches are redrawn; remaining pixels retain the baseline.',
                              'Cross-frame agreement is local consistency, not independent geometry validation.',
                              'Unchanged surface_data is the parent cache; redraw_data describes the new view.',
                              'Wiki registration to source views and gameplay navigation are not yet integrated.'])
    meta.update(projected_redraw=audit, map_render_mode='projected_raw_patches', navigation_ready=False,
                image_sha256=hashlib.sha256((output / 'map.png').read_bytes()).hexdigest())
    (output / 'map.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    (output / 'redraw_report.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in audit.items() if not isinstance(v, (dict, list))}), flush=True)
    for name, expected in hashes.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Baseline modified')
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--regions', action='store_true', help='Use source-road regions and raw-frame validation.')
    args = parser.parse_args()
    cv2.setNumThreads(2)
    redraw(args.source, args.output, args.regions)
