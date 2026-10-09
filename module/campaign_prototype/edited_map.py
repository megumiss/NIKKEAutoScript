"""读取人工道路覆盖，缓存身份只随道路修订变化，不受收集品标注影响。"""

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from dev_tools.map_terrain import render_terrain, terrain_colors


def edited_roads(package, metadata, baseline=None):
    """将 annotations.json 的人工道路操作应用于绑定的底图或给定 baseline。

    返回二值道路、覆盖掩码和仅由道路操作计算的哈希；1 表示擦除、2 表示补路，收集品修改不改变该哈希。
    """
    package = Path(package)
    document = package / 'annotations.json'
    annotations = json.loads(document.read_text(encoding='utf-8')) if document.exists() else {}
    if annotations and annotations.get('image_sha256') != metadata['image_sha256']:
        raise ValueError('Road edits belong to a different map')
    edits = annotations.get('terrain_edits', [])
    image, override = render_terrain(Image.open(package / 'map.png').convert('RGB'), edits, terrain_colors(metadata))
    override = np.asarray(override)
    if baseline is None:
        color = terrain_colors(metadata)['add']
        rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
        road = np.all(np.asarray(image) == rgb, axis=2).astype(np.uint8)
    else:
        road = np.asarray(baseline, np.uint8).copy()
        road[override == 1] = 0
        road[override == 2] = 1
    digest = hashlib.sha256(json.dumps(edits, sort_keys=True).encode()).hexdigest()
    return road, override, digest


def road_distance(road, point):
    """返回地图点到最近道路像素的欧氏距离，道路掩码应为 0/1。

    非法、非有限或越界点返回无穷大；合法点按向下取整的像素索引查询距离场。
    """
    point = np.asarray(point, float)
    if point.shape != (2,) or not np.isfinite(point).all() or np.any(point < 0) or np.any(point >= road.shape[::-1]):
        return float('inf')
    x, y = np.floor(point).astype(int)
    return float(cv2.distanceTransform(1 - road, cv2.DIST_L2, 5)[y, x])
