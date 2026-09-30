"""将绑定原始扫描的多表面模型导出为同坐标俯视地图；保留分层数据与未通过的验收项。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import cv2
import numpy as np
from scipy.spatial import cKDTree

from dev_tools.map_annotator import COORDINATES
from dev_tools.minimap_reconstruct import map_orientation, terrain, terrain_crop_bounds
from dev_tools.minimap_reference import frame_valid
from module.campaign_prototype.surface_geometry import SurfaceGeometry, orthographic_chart
from module.campaign_prototype.surface_motion import project


def sha(path):
    """绑定模型、截图与输出，防止跨次扫描混用坐标。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def observation_labels(images, tracks, model, max_distance=100):
    """用已观测且重投影合格的道路特征限定取色表面，其他表面的路面不计作正观测。"""
    world, labels = np.asarray(model['world']), np.asarray(model['labels'])
    projection = np.asarray(model['projection'])
    vertical, camera = np.asarray(model['vertical_origin']), np.asarray(model['camera'])
    support = [[] for _ in images]
    identities = [[] for _ in images]
    for index, track in enumerate(tracks):
        if labels[index] < 0:
            continue
        for frame, screen in zip(track['frames'], track['screen']):
            plane = vertical + (world[index, :2] - vertical - camera[frame]) / (1 - world[index, 2])
            if np.linalg.norm(project(np.linalg.inv(projection), plane) - screen) <= 3:
                support[frame].append(screen)
                identities[frame].append(labels[index])
    shape = images[0].shape[:2]
    pixels = np.indices(shape)[::-1].transpose(1, 2, 0).reshape(-1, 2)
    result = []
    for points, identifiers in zip(support, identities):
        labels = np.full(len(pixels), -1, np.int16)
        if points:
            distance, nearest = cKDTree(points).query(pixels)
            accepted = distance <= max_distance
            labels[accepted] = np.asarray(identifiers)[nearest[accepted]]
        result.append(labels.reshape(shape))
    return result


def validate_views(images, valid, masks, coverage, matrices, held):
    """按表面回投全部道路；候选表面重叠另行报告，不能仅凭并集 IoU 放行导航。"""
    records = []
    for frame in held:
        prediction = np.zeros(valid.shape, np.uint8)
        covered = np.zeros_like(prediction)
        for label, mask in enumerate(masks):
            inverse = np.linalg.inv(matrices[label, frame])
            prediction |= cv2.warpPerspective(mask.astype(np.uint8), inverse, valid.shape[::-1],
                                              flags=cv2.INTER_NEAREST)
            covered |= cv2.warpPerspective((coverage[label] >= 3).astype(np.uint8), inverse, valid.shape[::-1],
                                           flags=cv2.INTER_NEAREST)
        common = (valid > 0) & (covered > 0)
        observed, prediction = terrain(images[frame]) > 0, prediction > 0
        union = int(np.sum(common & (observed | prediction)))
        records.append({'frame': frame, 'coverage': float(common.sum() / max(1, valid.sum())),
                        'road_iou': float(np.sum(common & observed & prediction) / union) if union else None})
    return records


def render(source, model_path, tracks_path, output):
    """导出与普通章节相同的图片／标注坐标；模型、层及来源矩阵放入额外的分层数据。"""
    source, model_path, tracks_path, output = [Path(p).resolve() for p in (source, model_path, tracks_path, output)]
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Choose a new output directory outside the source scan')
    model = json.loads(model_path.read_text(encoding='utf-8'))
    tracks = json.loads(tracks_path.read_text(encoding='utf-8'))
    if not model.get('converged') or model.get('tracks_sha256') != sha(tracks_path):
        raise ValueError('Model does not bind converged geometry and its observations')
    scan = json.loads((source / 'scan.json').read_text(encoding='utf-8'))
    hashes = model['source_sha256']
    if (scan.get('version') != 2 or scan.get('status') != 'roads_exhausted'
            or set(hashes) != {'scan.json', *(frame['file'] for frame in scan['frames'])}):
        raise ValueError('Expected a completed scan with a complete model input manifest')
    images = []
    for name, expected in hashes.items():
        if Path(name).name != name or sha(source / name) != expected:
            raise ValueError('Model and source frame hashes differ')
    for index, frame in enumerate(scan['frames']):
        if frame['id'] != index:
            raise ValueError('Expected sequential source frame IDs')
        image = cv2.imread(str(source / frame['file']))
        if image is None:
            raise ValueError('Unreadable scan frame')
        images.append(image)
    world, labels = np.asarray(model['world']), np.asarray(model['labels'])
    if model.get('coefficient_scale') != 500:
        raise ValueError('Unknown surface coefficient scale')
    planes = np.asarray(model['planes'], float) / [500, 500, 1]
    if (len(images) < 5 or len(model['camera']) != len(images) or world.shape != (len(tracks), 3)
            or labels.shape != (len(tracks),) or not np.isfinite(world).all()
            or np.any(world[:, 2] >= .95) or np.any(labels >= len(planes))
            or any(image.shape != images[0].shape for image in images)):
        raise ValueError('Model, surface labels and image dimensions differ')
    shape = images[0].shape[:2]
    correction = orthographic_chart(model['projection'], model['focal_px'], shape)
    orientation = map_orientation(correction @ model['projection'], scan['warp_size'], shape, 'screen_ne_up')[0]
    display = np.eye(3)
    display[:2, :2] = orientation[:2, :2] @ correction[:2, :2]
    points = project(display, world[:, :2])
    low = np.floor(points.min(axis=0) - 100).astype(int)
    high = np.ceil(points.max(axis=0) + 100).astype(int)
    size = tuple(int(v) for v in high - low)
    if min(size) <= 0 or max(size) > 16000 or np.prod(size) > 20_000_000:
        raise ValueError('Implausible orthographic canvas')
    display[:2, 2] = -low
    surfaces = {f'surface_{index}': plane for index, plane in enumerate(planes)}
    geometry = SurfaceGeometry(model['projection'], model['vertical_origin'], model['camera'], surfaces, display)
    shape = size[::-1]
    world_pixels = project(np.linalg.inv(display), np.indices(shape)[::-1].transpose(1, 2, 0).reshape(-1, 2))
    segmented = observation_labels(images, tracks, model)
    roads = [terrain(image) for image in images]
    valid = frame_valid(images[0].shape[:2])
    weight = cv2.distanceTransform(valid, cv2.DIST_L2, 3)
    selected = [frame for frame in range(len(images)) if frame % 5 != 4]
    held = [frame for frame in range(len(images)) if frame % 5 == 4]
    masks, colors, owners, coverages, probabilities, matrices = [], [], [], [], [], []
    for label, (identifier, plane) in enumerate(surfaces.items()):
        supported = world[labels == label, :2]
        if len(supported) < 3:
            raise ValueError('Surface lacks observed feature support')
        height = world_pixels @ plane[:2] + plane[2]
        domain = ((cKDTree(supported).query(world_pixels)[0] < 160) & (height > -.3) & (height < .5)).reshape(shape)
        coverage, votes = np.zeros(shape, np.float32), np.zeros(shape, np.float32)
        reference = np.zeros((*shape, 3), np.uint8)
        owner, best = np.full(shape, -1, np.int16), np.zeros(shape, np.float32)
        transforms = np.asarray([geometry.roi_to_map(frame, identifier) for frame in range(len(images))])
        for frame in selected:
            local = segmented[frame] == label
            visible = cv2.warpPerspective(valid * ((roads[frame] == 0) | local), transforms[frame], size,
                                          flags=cv2.INTER_NEAREST).astype(bool) & domain
            road = cv2.warpPerspective((roads[frame] * local).astype(np.uint8), transforms[frame], size,
                                       flags=cv2.INTER_NEAREST) > 0
            coverage += visible
            votes += visible & road
            score = cv2.warpPerspective(weight, transforms[frame], size) * visible
            choose = score > best
            color = cv2.warpPerspective(images[frame], transforms[frame], size)
            reference[choose], owner[choose], best[choose] = color[choose], frame, score[choose]
        probability = np.divide(votes, coverage, out=np.zeros_like(votes), where=coverage > 0)
        masks.append((probability > .85) & (votes >= 3))
        colors.append(reference)
        owners.append(owner)
        coverages.append(coverage)
        probabilities.append(probability)
        matrices.append(transforms)
        print(f'{identifier}: {int(masks[-1].sum())} road pixels', flush=True)
    masks, matrices, coverages, probabilities = map(np.asarray, (masks, matrices, coverages, probabilities))
    # 背景沿主要参考平面保留完整取色；道路再按各自高度覆盖，避免只累积蓝色道路样本。
    reference = colors[0].copy()
    surface = np.full(shape, -1, np.int16)
    source_frame = owners[0].copy()
    texture_surface = np.where(source_frame >= 0, 0, -1).astype(np.int16)
    best_height = np.full(shape, -np.inf)
    for label, plane in enumerate(planes):
        height = (world_pixels @ plane[:2] + plane[2]).reshape(shape)
        choose = masks[label] & (height > best_height)
        reference[choose], source_frame[choose] = colors[label][choose], owners[label][choose]
        surface[choose], best_height[choose] = label, height[choose]
        texture_surface[choose] = label
    crop = terrain_crop_bounds(masks.any(axis=0).astype(np.float32))
    left, top, right, bottom = crop['box']
    area = np.s_[top:bottom, left:right]
    reference, surface, source_frame = reference[area], surface[area], source_frame[area]
    texture_surface = texture_surface[area]
    masks = masks[:, top:bottom, left:right]
    coverages = coverages[:, top:bottom, left:right]
    probabilities = probabilities[:, top:bottom, left:right]
    translation = np.array([[1., 0, -left], [0, 1., -top], [0, 0, 1.]])
    matrices, display = translation @ matrices, translation @ display
    map_image = np.full_like(reference, (44, 35, 28))
    map_image[masks.any(axis=0)] = (186, 139, 59)
    checks = validate_views(images, valid, masks, coverages, matrices, held)
    records = [{'id': identifier, 'height_plane': plane.tolist()} for identifier, plane in surfaces.items()]
    output.mkdir(parents=True)
    (output / 'source').mkdir()
    for name, expected in hashes.items():
        shutil.copyfile(source / name, output / 'source' / name)
        if sha(output / 'source' / name) != expected or sha(source / name) != expected:
            raise RuntimeError('Scan changed while exporting')
    for name, image in [('map.png', map_image), ('reference.png', reference)]:
        if not cv2.imwrite(str(output / name), image):
            raise OSError(f'Could not write {name}')
    np.savez_compressed(output / 'source/surface_map.npz', masks=masks, probability=probabilities,
                        coverage=coverages, matrices=matrices, world_to_map=display,
                        source_frame=source_frame, surface_id=surface, texture_surface_id=texture_surface,
                        roi_surface_id=np.asarray(segmented))
    shutil.copyfile(model_path, output / 'surface_model.json')
    shutil.copyfile(tracks_path, output / 'tracks.json')
    metadata = dict(schema_version=2, chapter=scan['chapter'], difficulty='normal', image='map.png',
                    reference_image='reference.png', coordinates=COORDINATES.copy(), size=list(surface.shape[::-1]),
                    coordinate_model='orthographic_surfaces', orientation='screen_ne_up', crop=crop,
                    image_sha256=sha(output / 'map.png'), reference_sha256=sha(output / 'reference.png'),
                    geometry_data='source/surface_map.npz', geometry_sha256=sha(output / 'source/surface_map.npz'),
                    source_sha256=hashes, model_sha256=sha(model_path), tracks_sha256=sha(tracks_path),
                    projection=model['projection'], vertical_origin=model['vertical_origin'],
                    camera=model['camera'],
                    world_to_map=display.tolist(), surfaces=records, texture_frames=selected, holdout_frames=held,
                    status='needs_surface_identity_and_movement_validation', navigation_ready=False,
                    capture=dict(status=scan['status'], unresolved_frontiers=scan.get('unresolved_frontiers'),
                                 whole_camera_domain_verified=bool(scan.get('whole_camera_domain_verified', False))),
                    validation=dict(texture_holdout=checks,
                                    overlapping_surface_pixels=int((masks.sum(axis=0) > 1).sum()),
                                    independent_geometry_verified=False, surface_identity_verified=False,
                                    field_clicks_verified=False),
                    limitations=['Multiple candidate surfaces can explain the same road; union IoU is not acceptance.',
                                 'Held-out textures still contributed to the geometry model.',
                                 'Surface masks and connections require review before routing.',
                                 'No chapter 40 field-click calibration; chapter 38 calibration cannot be reused.'])
    root = Path(__file__).resolve().parents[1]
    metadata['code_sha256'] = {name: sha(root / name) for name in [
        'dev_tools/minimap_orthographic.py', 'dev_tools/minimap_reconstruct.py', 'dev_tools/minimap_reference.py',
        'module/campaign_prototype/surface_geometry.py', 'module/campaign_prototype/surface_motion.py']}
    (output / 'map.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    annotations = dict(schema_version=1, image='map.png', image_sha256=metadata['image_sha256'],
                       coordinates=COORDINATES.copy(), objects=[], connections=[])
    (output / 'annotations.json').write_text(json.dumps(annotations, indent=2), encoding='utf-8')
    return metadata


def main():
    """消费具有输入哈希的已拟合表面模型，不把诊断结果改名为合格平面导航包。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--tracks', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    result = render(args.source, args.model, args.tracks, args.output)
    print(json.dumps({key: result[key] for key in ['size', 'coordinate_model', 'status', 'navigation_ready']}))


if __name__ == '__main__':
    main()
