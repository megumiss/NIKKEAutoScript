"""按原始帧中的道路／高度区域重绘，使用原始帧互验而非旧 map 像素筛选。"""

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

import cv2
import numpy as np

from dev_tools.minimap_layered import project
from dev_tools.minimap_projected_redraw import warp_patch
from module.campaign_prototype.local_projection import frame_plane_to_map


def plane_ratio(plane, rectified):
    return ((rectified - plane['center']) / plane['coordinate_scale']) @ np.asarray(plane['ratio_plane'][:2]) \
        + plane['ratio_plane'][2]


def source_to_other(metadata, frame, other, plane):
    """同一道路平面的跨帧运动由相机位移与局部视差共同确定。"""
    slope = np.asarray(plane['ratio_plane'][:2]) / plane['coordinate_scale']
    denominator = np.r_[slope, plane['ratio_plane'][2] - slope @ plane['center']]
    delta = np.asarray(metadata['camera'][other]) - metadata['camera'][frame]
    motion = np.eye(3)
    motion[:2] -= np.outer(delta, denominator)
    matrix = np.asarray(metadata['projection'])
    return np.linalg.inv(matrix) @ motion @ matrix


def source_regions(road, clear, cache, planes, rectified):
    """区域来自原帧的道路和高度；形态运算只界定投影范围，不修改道路内容。"""
    for label, plane in enumerate(planes):
        seed = ((cache['local_surface'] == label) & (road > 0) & (clear > 0)).astype(np.uint8)
        connected = cv2.morphologyEx(seed, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(connected, connectivity=8)
        ratio = plane_ratio(plane, rectified).reshape(road.shape)
        known = cache['ratio']
        compatible = ((road == 0) | ~np.isfinite(known) | (np.abs(known - ratio) < .06))
        compatible &= (ratio > .76) & (ratio < 1.74)
        for index in range(1, count):
            if stats[index, cv2.CC_STAT_AREA] < 600:
                continue
            component = (labels == index).astype(np.uint8)
            domain = (cv2.dilate(component, np.ones((19, 19), np.uint8)) > 0) & compatible & (clear > 0)
            if np.count_nonzero(domain & (road > 0)) >= 500:
                yield label, plane, domain


def check_view(source_road, other_road, other_clear, transform, domain):
    """在来源区域内检查另一原始帧；空白也参与轮廓检查。"""
    size = source_road.shape[::-1]
    flags = cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP
    visible = cv2.warpPerspective(other_clear, transform, size, flags=flags) > 0
    observed = cv2.warpPerspective(other_road, transform, size, flags=flags) > 0
    common = domain & visible
    fraction = common.sum() / max(1, domain.sum())
    union = common & ((source_road > 0) | observed)
    iou = np.count_nonzero(common & (source_road > 0) & observed) / max(1, union.sum())
    first = source_road > 0
    kernel = np.ones((3, 3), np.uint8)
    safe = cv2.erode(common.astype(np.uint8), kernel) > 0
    edges = [cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_GRADIENT, kernel) > 0
             for mask in [first, observed]]
    counts = [int(np.count_nonzero(edge & safe)) for edge in edges]
    boundary_error = float('inf')
    if min(counts) >= 40:
        distances = [cv2.distanceTransform((~edge).astype(np.uint8), cv2.DIST_L2, 5) for edge in edges]
        boundary_error = max(float(np.percentile(distances[1][edges[0] & safe], 90)),
                             float(np.percentile(distances[0][edges[1] & safe], 90)))
    return float(fraction), float(iou), common, boundary_error


def redraw_regions(source, output, meta, hashes, images, roads, clear, caches, baseline, stop_file=None):
    shape = roads[0].shape
    sy, sx = np.indices(shape)
    rectified = project(meta['projection'], np.column_stack([sx.ravel(), sy.ravel()]))
    before = cv2.imread(str(source / 'map.png'))
    result, reference = before.copy(), cv2.imread(str(source / 'reference.png'))
    height, width = before.shape[:2]
    owner = np.full((height, width), -1, np.int16)
    ids = np.full((height, width), -1, np.int16)
    roi = np.full((height, width, 2), np.nan, np.float32)
    best = np.full((height, width), -np.inf, np.float32)
    records, masks = [], []
    rejected = Counter()
    cameras = np.asarray(meta['camera'])
    attempted = 0
    for frame in range(len(images)):
        if stop_file is not None and Path(stop_file).exists():
            raise KeyboardInterrupt('Stopped during source-region redraw.')
        if frame % 10 == 0:
            print(f'Checking source road regions {frame}/{len(images)}; accepted={len(records)}', flush=True)
        displacement = np.linalg.norm(cameras - cameras[frame], axis=1)
        neighbors = np.flatnonzero((displacement >= 60) & (displacement <= 200))
        neighbors = neighbors[np.argsort(np.abs(displacement[neighbors] - 110))[:8]]
        if len(neighbors) < 2:
            rejected['frames_without_camera_baseline'] += 1
            continue
        planes = meta['frame_support'][frame]['local_planes']
        for label, plane, domain in source_regions(roads[frame], clear[frame], caches[frame], planes, rectified):
            attempted += 1
            checks = []
            predicted_ratio = plane_ratio(plane, rectified).reshape(shape)
            for other in neighbors:
                transform = source_to_other(meta, frame, int(other), plane)
                fraction, iou, common, boundary = check_view(
                    roads[frame], roads[other], clear[other], transform, domain)
                if fraction < .6:
                    rejected['view_insufficient_overlap'] += 1
                elif iou < .9:
                    rejected['view_road_disagreement'] += 1
                elif boundary > 2:
                    rejected['view_boundary_disagreement_or_uninformative'] += 1
                else:
                    other_ratio = cv2.warpPerspective(caches[other]['ratio'], transform, shape[::-1],
                                                      flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                                      borderValue=float('nan'))
                    measured = common & (roads[frame] > 0) & np.isfinite(other_ratio)
                    disagreement = np.abs(predicted_ratio[measured] - other_ratio[measured])
                    if (measured.sum() < 200 or np.median(disagreement) > .025
                            or np.percentile(disagreement, 80) > .05):
                        rejected['view_independent_depth_disagreement'] += 1
                    else:
                        checks.append((iou, int(other), fraction, common, boundary,
                                       float(np.median(disagreement))))
            checks.sort(key=lambda item: item[0], reverse=True)
            pair = next(((a, b) for i, a in enumerate(checks) for b in checks[i + 1:]
                         if np.linalg.norm(cameras[a[1]] - cameras[b[1]]) >= 40), None)
            if pair is None:
                rejected['regions_without_two_independent_camera_positions'] += 1
                continue
            first, second = pair
            valid = first[3] & second[3]
            if valid.sum() < 600:
                rejected['regions_insufficient_common_area'] += 1
                continue
            final_checks = [check_view(roads[frame], roads[c[1]], clear[c[1]],
                                       source_to_other(meta, frame, c[1], plane), valid) for c in pair]
            if any(check[1] < .9 or check[3] > 2 for check in final_checks):
                rejected['regions_final_domain_disagreement'] += 1
                continue
            matrix = frame_plane_to_map(meta, frame, plane)
            yy, xx = np.nonzero(valid)
            mapped = project(matrix, np.column_stack([xx, yy]))
            low, high = np.floor(mapped.min(axis=0)).astype(int), np.ceil(mapped.max(axis=0)).astype(int) + 1
            left, top = np.maximum(low, [0, 0])
            right, bottom = np.minimum(high, [width, height])
            if left >= right or top >= bottom:
                rejected['regions_outside_fixed_canvas'] += 1
                continue
            rect = tuple(map(int, [left, top, right, bottom]))
            visible = warp_patch(valid.astype(np.uint8), matrix, rect) > 0
            area = np.s_[top:bottom, left:right]
            # 选择整片观测的质量，不因某个像素是道路而加分；空白具有同等覆盖权。
            quality = min(first[0], second[0]) + .04 * np.log1p(valid.sum()) / 12
            score = np.float32(quality)
            selected = visible & (score > best[area])
            if not selected.any():
                rejected['regions_superseded_by_better_source'] += 1
                continue
            road = warp_patch(roads[frame], matrix, rect) > 0
            painted = np.full((*road.shape, 3), (44, 35, 28), np.uint8)
            painted[road] = (186, 139, 59)
            result[area][selected] = painted[selected]
            color = warp_patch(images[frame], matrix, rect, cv2.INTER_LINEAR)
            reference[area][selected] = color[selected]
            py, px = np.indices(road.shape)
            source_points = project(np.linalg.inv(matrix), np.column_stack([px.ravel() + left, py.ravel() + top]))
            roi[area][selected] = source_points.reshape(*road.shape, 2)[selected]
            best[area][selected], owner[area][selected], ids[area][selected] = score, frame, len(records)
            records.append(dict(id=len(records), rect=list(rect), source_frame=frame, local_surface=int(label),
                                roi_to_map=matrix.tolist(), ratio_plane=plane,
                                checks=[dict(frame=c[1], road_iou=c[0], visible_fraction=c[2],
                                             boundary_p90_px=c[4], depth_median_difference=c[5],
                                             final_road_iou=final[1], final_boundary_p90_px=final[3],
                                             source_to_check=source_to_other(meta, frame, c[1], plane).tolist())
                                        for c, final in zip(pair, final_checks)],
                                quality=float(score), source_domain=f'regions/{len(records):04d}.png'))
            masks.append(valid.astype(np.uint8) * 255)
    output.mkdir(parents=True)
    (output / 'regions').mkdir()
    for record, mask in zip(records, masks):
        cv2.imwrite(str(output / record['source_domain']), mask)
        record['painted_pixels'] = int(np.count_nonzero(ids == record['id']))
    for folder in ['source', 'depth']:
        shutil.copytree(source / folder, output / folder)
    for name in ['surface_data.npz', 'tracks.json', 'surface_model.json']:
        shutil.copyfile(source / name, output / name)
    for name, image in [('map.png', result), ('reference.png', reference)]:
        if not cv2.imwrite(str(output / name), image):
            raise OSError(f'Could not write {name}')
    np.savez_compressed(output / 'redraw_data.npz', patch_id=ids, source_frame=owner, source_roi=roi,
                        road_mask=np.all(result == (186, 139, 59), axis=2))
    audit = dict(input=str(source), input_sha256=hashes, method='source_road_regions',
                 attempted_regions=attempted, accepted_regions=len(records),
                 active_regions=sum(r['painted_pixels'] > 0 for r in records),
                 painted_pixels=int((ids >= 0).sum()), changed_pixels=int(np.any(before != result, axis=2).sum()),
                 rejection_events=dict(rejected), patches=records,
                 old_map_pixel_constraints=False,
                 code_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              [Path(__file__), Path('dev_tools/minimap_projected_redraw.py'),
                               Path('module/campaign_prototype/local_projection.py')]},
                 limitations=['Existing camera and source-plane estimates define fixed map coordinates.',
                              'Two other views check raw roads, not independent global geometry.',
                              'Only accepted source domains are redrawn. Remaining pixels retain the parent map.',
                              'The parent surface_data is unchanged; redraw_data describes the displayed view.',
                              'No gameplay navigation or Wiki localization acceptance.'])
    meta.update(projected_redraw=audit, map_render_mode='projected_raw_regions', navigation_ready=False,
                image_sha256=hashlib.sha256((output / 'map.png').read_bytes()).hexdigest())
    (output / 'map.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    (output / 'redraw_report.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    for name, expected in hashes.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Baseline changed')
    summary = {k: v for k, v in audit.items() if k not in ['patches', 'input_sha256', 'code_sha256']}
    print(json.dumps(summary), flush=True)
    return audit
