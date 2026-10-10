"""章节道路连通数据及人工分隔；与定位使用的道路图像分开保存。"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from .map_terrain import render_terrain, terrain_colors
else:
    from map_terrain import render_terrain, terrain_colors


ALGORITHM = {'name': 'road_grid_v2', 'step': 8, 'clearance': 6, 'diagonal': 'no_corner_cutting'}


def validate_edits(edits, size):
    if not isinstance(edits, list) or len(edits) > 1000:
        raise ValueError('连通修订必须是列表，最多 1000 条。')
    identifiers = set()
    for edit in edits:
        if not isinstance(edit, dict) or edit.get('operation') != 'cut':
            raise ValueError('连通修订仅支持分隔线；跨区域请使用电梯传送关系。')
        identifier, points = edit.get('id'), edit.get('points')
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100 or identifier in identifiers:
            raise ValueError('连通修订 ID 无效或重复。')
        identifiers.add(identifier)
        if not isinstance(points, list) or not 2 <= len(points) <= 2000:
            raise ValueError('连通修订至少需要两个顶点，最多 2000 个。')
        if type(edit.get('width')) is not int or not 8 <= edit['width'] <= 160:
            raise ValueError('分隔线宽度必须为 8～160 地图像素。')
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(
                    type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v < bound
                    for v, bound in zip(point, size)):
                raise ValueError('连通修订坐标无效或超出地图。')
        if len({tuple(p) for p in points}) < 2:
            raise ValueError('连通修订需要两个不同顶点。')


def package_road(package, metadata, annotations):
    package = Path(package)
    with Image.open(package / 'map.png') as image:
        edited, override = render_terrain(image, annotations.get('terrain_edits', []), terrain_colors(metadata))
    cache = package / 'source/map_data.npz'
    if metadata.get('coordinate_model') is None and cache.exists():
        with np.load(cache, allow_pickle=False) as data:
            road = (data['terrain_probability'] >= .5).astype(np.uint8)
        if road.shape != (metadata['size'][1], metadata['size'][0]):
            raise ValueError('道路缓存尺寸与地图不一致。')
        override = np.asarray(override)
        road[override == 1], road[override == 2] = 0, 1
        return road
    color = terrain_colors(metadata)['add']
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    return np.all(np.asarray(edited) == rgb, axis=2).astype(np.uint8)


def binding(road, image_hash, edits):
    return dict(image_sha256=image_hash, size=list(road.shape[::-1]),
                road_sha256=hashlib.sha256(road.astype(np.uint8).tobytes()).hexdigest(),
                edits_sha256=hashlib.sha256(json.dumps(edits, sort_keys=True).encode()).hexdigest())


def encode_labels(labels):
    rows, regions = [], []
    for row in labels:
        changes = np.flatnonzero(np.diff(np.r_[0, row, 0]))
        rows.append([[int(a), int(b), int(row[a])] for a, b in zip(changes[:-1], changes[1:]) if row[a]])
    for identifier in sorted(set(np.unique(labels)) - {0}):
        y, x = np.nonzero(labels == identifier)
        center = np.array([x.mean(), y.mean()])
        i = np.argmin(np.linalg.norm(np.column_stack([x, y]) - center, axis=1))
        regions.append(dict(id=int(identifier), nodes=len(x), center=[int(x[i] * 8), int(y[i] * 8)],
                            bounds=[int(x.min() * 8), int(y.min() * 8), int(x.max() * 8), int(y.max() * 8)]))
    return dict(rows=rows, regions=regions)


def decode_labels(data, shape):
    labels = np.zeros(shape, np.int32)
    if len(data['rows']) != shape[0]:
        raise ValueError('连通数据行数不正确。')
    for y, runs in enumerate(data['rows']):
        end = 0
        for start, stop, label in runs:
            if (any(type(v) is not int for v in (start, stop, label))
                    or not end <= start < stop <= shape[1] or label <= 0):
                raise ValueError('连通数据网格无效。')
            labels[y, start:stop], end = label, stop
    return labels


def build(road, image_hash, edits):
    validate_edits(edits, road.shape[::-1])
    clearance = cv2.distanceTransform(road.astype(np.uint8), cv2.DIST_L2, 5)[::8, ::8]
    mask = (clearance > 6).astype(np.uint8)
    # 禁止穿墙角的八邻域与四邻域具有相同的连通分区。
    _, automatic = cv2.connectedComponents(mask, connectivity=4)
    cut = Image.new('L', road.shape[::-1], 0)
    draw = ImageDraw.Draw(cut)
    for edit in edits:
        draw.line([tuple(p) for p in edit['points']], fill=255, width=edit['width'], joint='curve')
    cut = np.asarray(cut)
    mask[cut[::8, ::8] > 0] = 0
    _, labels = cv2.connectedComponents(mask, connectivity=4)
    return dict(schema_version=1, algorithm=ALGORITHM, binding=binding(road, image_hash, edits),
                automatic=encode_labels(automatic), effective=encode_labels(labels))


def read_bound(path, road, image_hash, edits):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if (data.get('schema_version') != 1 or data.get('algorithm') != ALGORITHM
            or data.get('binding') != binding(road, image_hash, edits)):
        raise ValueError('连通数据与当前道路或修订不一致，请在地图标注工具保存并重新计算。')
    decode_labels(data['automatic'], road[::8, ::8].shape)
    decode_labels(data['effective'], road[::8, ::8].shape)
    return data


def write_data(path, data):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def ensure_data(package, metadata, annotations, persist=False):
    road = package_road(package, metadata, annotations)
    edits = annotations.get('connectivity_edits', [])
    path = Path(package) / 'connectivity.json'
    if path.exists():
        try:
            return read_bound(path, road, metadata['image_sha256'], edits)
        except (ValueError, KeyError, TypeError):
            pass
    data = build(road, metadata['image_sha256'], edits)
    if persist:
        write_data(path, data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('data/resources/maps'))
    args = parser.parse_args()
    if __package__:
        from .map_annotator import AnnotationStore
    else:
        from map_annotator import AnnotationStore
    store = AnnotationStore(args.root)
    for item in store.catalog():
        package = store.package(item['id'])
        loaded = store.load(item['id'])
        metadata = json.loads((package / 'map.json').read_text(encoding='utf-8'))
        data = ensure_data(package, metadata, loaded['annotations'], persist=True)
        print(json.dumps(dict(map=item['id'], automatic=len(data['automatic']['regions']),
                              effective=len(data['effective']['regions'])),
                         ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
