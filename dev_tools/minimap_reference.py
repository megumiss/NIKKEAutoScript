"""保留完整截图生成实拍参考图，再提取道路；使用共同投影平面，仅用于离线复核。"""

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from dev_tools.minimap_layered import project
from dev_tools.minimap_reconstruct import terrain, terrain_crop_bounds


def frame_homography(projection, camera, vertical, rotation, origin, ratio):
    """把整帧投到固定参考平面；高低道路的视差保留为待核对的拼接误差。"""
    offset = np.asarray(vertical) * (1 - 1 / ratio) + camera
    plane = np.array([[1 / ratio, 0, offset[0]], [0, 1 / ratio, offset[1]], [0, 0, 1.]])
    display = np.eye(3)
    display[:2, :2] = rotation
    display[:2, 2] = -np.asarray(origin)
    return display @ plane @ projection


def frame_valid(shape):
    """排除面板边缘和右下角控件，保留道路之间的空白作为真实图像内容。"""
    valid = np.ones(shape, np.uint8)
    valid[:15] = valid[-15:] = 0
    valid[:, :15] = valid[:, -15:] = 0
    valid[-50:, -90:] = 0
    return valid


def compose(images, matrices, valid, size, selected):
    """按视野中心距离选择完整观测，同权重保持已有来源；空白也参与覆盖。"""
    reference = np.zeros((size[1], size[0], 3), np.uint8)
    source_frame = np.full((size[1], size[0]), -1, np.int16)
    best = np.zeros(source_frame.shape, np.float32)
    weight = cv2.distanceTransform(valid, cv2.DIST_L2, 3)
    for index in selected:
        score = cv2.warpPerspective(weight, matrices[index], size)
        visible = cv2.warpPerspective(valid, matrices[index], size, flags=cv2.INTER_NEAREST) > 0
        choose = visible & (score > best)
        color = cv2.warpPerspective(images[index], matrices[index], size, flags=cv2.INTER_LINEAR)
        reference[choose] = color[choose]
        source_frame[choose] = index
        best[choose] = score[choose]
    return reference, source_frame


def inspect_holdout(images, matrices, valid, mask, source_frame, held):
    """把道路图投回未取色的帧，统计覆盖和双向轮廓误差；共享几何不算独立验收。"""
    shape = images[0].shape[:2]
    records, errors = [], []
    for index in held:
        inverse = np.linalg.inv(matrices[index])
        observed = terrain(images[index]) > 0
        predicted = cv2.warpPerspective(mask, inverse, shape[::-1], flags=cv2.INTER_NEAREST) > 0
        coverage = cv2.warpPerspective((source_frame >= 0).astype(np.uint8), inverse, shape[::-1],
                                       flags=cv2.INTER_NEAREST) > 0
        common = (valid > 0) & coverage
        interior = cv2.erode(common.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        edges = [cv2.morphologyEx(road.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
                 for road in [observed, predicted]]
        values = []
        for first, second in [edges, edges[::-1]]:
            if not second.any():
                continue
            distance = cv2.distanceTransform((~second).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
            values.extend(distance[first & interior].tolist())
        errors.extend(values)
        union = int(np.count_nonzero(common & (observed | predicted)))
        records.append(dict(frame=index, covered_fraction=float(common.sum() / max(1, (valid > 0).sum())),
                            road_union_pixels=union,
                            road_iou=float(np.count_nonzero(common & observed & predicted) / union) if union else None,
                            contour_samples=len(values),
                            contour_median_px=float(np.median(values)) if values else None,
                            contour_p95_px=float(np.percentile(values, 95)) if values else None))
    scores = [record['road_iou'] for record in records if record['road_iou'] is not None]
    return dict(frames=records, road_iou_median=float(np.median(scores)) if scores else None,
                road_iou_min=min(scores) if scores else None, contour_samples=len(errors),
                contour_median_px=float(np.median(errors)) if errors else None,
                contour_p95_px=float(np.percentile(errors, 95)) if errors else None)


def render(source, output, holdout_stride=5):
    """在独立目录导出实拍图、直接提取的道路、逐像素来源和未取色帧回投结果。"""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Choose a new directory outside the source package')
    if holdout_stride < 2:
        raise ValueError('Holdout stride must be at least two')
    meta = json.loads((source / 'map.json').read_text(encoding='utf-8'))
    model = json.loads((source / 'surface_model.json').read_text(encoding='utf-8'))
    scan = json.loads((source / 'source/scan.json').read_text(encoding='utf-8'))
    if meta.get('coordinate_model') != 'local_parallax' or scan.get('status') != 'roads_exhausted':
        raise ValueError('Expected a completed capture and fitted layered geometry')
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
              for name in ['map.json', 'surface_model.json', 'source/scan.json']}
    images = []
    for index, frame in enumerate(scan['frames']):
        if frame['id'] != index or Path(frame['file']).name != frame['file']:
            raise ValueError('Expected sequential frames with plain relative filenames')
        name = 'source/' + frame['file']
        raw = (source / name).read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        if hashes[name] != meta['source_sha256'][frame['file']]:
            raise ValueError('Source frame hash mismatch')
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('Unreadable source frame')
        images.append(image)
    if len(images) < holdout_stride:
        raise ValueError('Insufficient frames to hold out a full image')
    shape = images[0].shape[:2]
    if any(image.shape[:2] != shape for image in images):
        raise ValueError('Frame dimensions differ')
    projection, rotation = np.asarray(meta['projection']), np.asarray(meta['rotation'])
    camera, vertical = np.asarray(meta['camera']), np.asarray(meta['vertical_origin'])
    ratio = float(np.median(model['geometry']['ratios']))
    if not np.isfinite(ratio) or ratio <= 0 or len(camera) != len(images):
        raise ValueError('Invalid shared reference plane or camera count')
    held = [index for index in range(len(images)) if index % holdout_stride == holdout_stride - 1]
    selected = [index for index in range(len(images)) if index not in held]
    matrices = np.asarray([frame_homography(projection, c, vertical, rotation, np.zeros(2), ratio) for c in camera])
    corners = np.array([[15, 15], [shape[1] - 15, 15], [15, shape[0] - 15],
                        [shape[1] - 15, shape[0] - 15]], float)
    bounds = np.concatenate([project(matrices[index], corners) for index in selected])
    if not np.isfinite(bounds).all():
        raise ValueError('Nonfinite reference canvas')
    origin = np.floor(bounds.min(axis=0)).astype(int) - 24
    size = np.ceil(bounds.max(axis=0)).astype(int) + 25 - origin
    if np.any(size <= 0) or np.any(size > 16000) or np.prod(size) > 20_000_000:
        raise ValueError('Implausible reference canvas')
    translation = np.array([[1., 0, -origin[0]], [0, 1., -origin[1]], [0, 0, 1.]])
    matrices = translation @ matrices
    valid = frame_valid(shape)
    print(f'Composing {len(selected)} full views; holding out {len(held)} textures', flush=True)
    reference, source_frame = compose(images, matrices, valid, tuple(size), selected)
    mask = terrain(reference)
    crop = terrain_crop_bounds(mask.astype(np.float32) / 255)
    left, top, right, bottom = crop['box']
    reference = reference[top:bottom, left:right]
    source_frame = source_frame[top:bottom, left:right]
    mask = mask[top:bottom, left:right]
    matrices = np.array([[1., 0, -left], [0, 1., -top], [0, 0, 1.]]) @ matrices
    origin += [left, top]
    map_image = np.full_like(reference, (44, 35, 28))
    map_image[mask > 0] = (186, 139, 59)
    covered = source_frame >= 0
    source_roi = np.full((*source_frame.shape, 2), np.nan, np.float32)
    for index in selected:
        y, x = np.nonzero(source_frame == index)
        if len(x):
            source_roi[y, x] = project(np.linalg.inv(matrices[index]), np.column_stack([x, y]))
    if set(np.unique(source_frame[covered])) & set(held):
        raise AssertionError('Held-out texture contributed to reference')
    validation = inspect_holdout(images, matrices, valid, mask, source_frame, held)
    output.mkdir(parents=True)
    for name, image in [('reference.png', reference), ('map.png', map_image), ('terrain_mask.png', mask)]:
        if not cv2.imwrite(str(output / name), image):
            raise OSError(f'Could not write {name}')
    np.savez_compressed(output / 'reference_data.npz', source_frame=source_frame, source_roi=source_roi,
                        matrices=matrices, shared_ratio=ratio, terrain=mask, projection=projection,
                        camera=camera, rotation=rotation, origin=origin, vertical_origin=vertical)
    report = dict(schema_version=2, chapter=meta['chapter'], difficulty=meta['difficulty'],
                  status='reference_experiment_needs_geometry_review', navigation_ready=False,
                  whole_camera_domain_verified=False, coordinate_model='shared_plane_reference',
                  coordinates=dict(unit='pixel', origin='top_left', x='right', y='down'),
                  image='map.png', reference_image='reference.png', size=list(mask.shape[::-1]),
                  image_sha256=hashlib.sha256((output / 'map.png').read_bytes()).hexdigest(),
                  reference_sha256=hashlib.sha256((output / 'reference.png').read_bytes()).hexdigest(),
                  texture_frames=selected, render_holdout_frames=held, shared_reference_ratio=ratio,
                  source=str(source), source_sha256=hashes, geometry_data='reference_data.npz',
                  code_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                               for name in ['minimap_reference.py', 'minimap_layered.py', 'minimap_reconstruct.py']},
                  validation=validation, reference_pixels=int(covered.sum()), road_pixels=int(np.count_nonzero(mask)),
                  crop=crop, limitations=[
                      'Full views share one reference plane; depth-dependent parallax remains at seams.',
                      'Held-out frames contribute no texture but were used by the cached geometry fit.',
                      'Contour and overlap checks are diagnostic, not independent camera or absolute accuracy.',
                      'No layer identity, cross-layer connectivity, live movement or whole-camera-domain validation.'])
    (output / 'map.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    for name, expected in hashes.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Source changed during rendering')
    print(json.dumps({k: v for k, v in report.items() if k not in
                      ['source_sha256', 'texture_frames', 'render_holdout_frames', 'validation']}), flush=True)
    print(json.dumps({k: v for k, v in validation.items() if k != 'frames'}), flush=True)
    return report


def main():
    """从已完成的分层缓存生成独立实拍实验目录，不覆盖正式包。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--holdout-stride', type=int, default=5)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    render(args.source, args.output, args.holdout_stride)


if __name__ == '__main__':
    main()
