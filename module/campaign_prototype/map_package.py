"""Validate one chapter package and its calibration before acquiring input."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from dev_tools.map_annotator import COORDINATES, validate_annotations
from . import settings


def matrix(value, shape=(3, 3)):
    """检查二维或三维方阵的形状、有限数值和可逆性，拒绝退化坐标变换。

    将输入转为浮点数组，默认要求 3×3，也可显式指定 2×2。
    形状错误、非有限元素或行列式接近零时抛出 ValueError；返回值可用于后续正逆投影。
    """
    result = np.asarray(value, dtype=float)
    if result.shape != shape or not np.isfinite(result).all() or abs(np.linalg.det(result)) < 1e-10:
        raise ValueError('Invalid coordinate transform')
    return result


def transform(value, point):
    """按齐次矩阵转换二维坐标并除以尺度，投影地平线附近不允许继续外推。

    point 是单个二维点，value 为可逆 3×3 齐次变换。
    返回完成齐次除法的二维数组；输入无效或分母接近零时拒绝，避免无穷大落点进入点击链。
    """
    point = np.asarray(point, dtype=float)
    if point.shape != (2,) or not np.isfinite(point).all():
        raise ValueError('Invalid coordinate point')
    projected = matrix(value) @ np.append(point, 1.)
    if abs(projected[2]) < 1e-8:
        raise ValueError('Point lies on projection horizon')
    return projected[:2] / projected[2]


def screenshot_to_client(point, screenshot_size, client_size=(1776, 999)):
    """按等比例截图尺寸换算客户区像素；裁剪或黑边导致的比例差异必须另行建模。

    point 属于未裁剪截图，尺寸参数均按宽、高排列，返回对应客户区浮点像素。
    要求截图与客户区宽高比一致且点在截图内部；黑边、裁剪及非等比缩放必须另建变换。
    """
    size = np.asarray(screenshot_size, float)
    client = np.asarray(client_size, float)
    point = np.asarray(point, float)
    if (size.shape != (2,) or client.shape != (2,) or point.shape != (2,)
            or not np.isfinite([*size, *client, *point]).all() or min(*size, *client) <= 0
            or np.any(point < 0) or np.any(point >= size)):
        raise ValueError('Invalid screenshot coordinate or size')
    if not np.isclose(size[0] / size[1], client[0] / client[1]):
        raise ValueError('Screenshot aspect ratio differs; crop/letterbox transform required')
    return point * client / size


class MapPackage:
    def __init__(self, path=None, chapter=None, difficulty=None):
        """在取得输入控制前绑定章节、难度、文件哈希、裁剪、投影及覆盖质量，并筛选同难度点目标。

        读取地图、标注、缓存、标定与 validation.json，严格校验身份、文件哈希、坐标和观测道路覆盖。
        修订道路覆盖原道路概率，targets 仅收录同难度且带唯一编号的点；任何不一致在取得输入前失败。
        """
        self.path = Path(path or settings.package).resolve()
        chapter = settings.chapter if chapter is None else chapter
        difficulty = settings.difficulty if difficulty is None else difficulty
        metadata = json.loads((self.path / 'map.json').read_text(encoding='utf-8'))
        manifest = json.loads((self.path / 'validation.json').read_text(encoding='utf-8'))
        annotations = json.loads((self.path / 'annotations.json').read_text(encoding='utf-8'))
        if metadata['chapter'] != chapter or metadata['difficulty'] != difficulty:
            raise ValueError('Wrong chapter or difficulty')
        if (metadata.get('schema_version') != 1 or metadata.get('coordinates') != COORDINATES
                or metadata.get('image') != 'map.png'):
            raise ValueError('Unsupported map coordinate convention')
        required = {'map.json', 'map.png', 'annotations.json', 'source/map_data.npz', 'calibration.json'}
        if set(manifest['sha256']) != required:
            raise ValueError('Incomplete package hash binding')
        for name, expected in manifest['sha256'].items():
            if hashlib.sha256((self.path / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f'Package hash mismatch: {name}')
        digest = manifest['sha256']['map.png']
        if digest != metadata['image_sha256'] or digest != annotations['image_sha256']:
            raise ValueError('Map and annotations refer to different images')
        if manifest['client'] != [1776, 999] or manifest['roi'] != [644, 280, 1130, 742]:
            raise ValueError('Package capture geometry differs from this prototype')
        capture = metadata['capture']
        registration = capture['registration']
        if (registration['status'] != 'joint_grid_road'
                or not 0 <= registration['residual_median_px'] <= 3
                or capture['excluded_unlocalized_frames']
                or capture['frames'] <= 0 or capture['localized_frames'] != capture['frames']):
            raise ValueError('Unqualified map registration')
        if capture['status'] != 'roads_exhausted' or capture['unresolved_frontiers']:
            raise ValueError('Map has unresolved road coverage')
        if (manifest['coverage'] != 'observed_roads'
                or not isinstance(capture['whole_camera_domain_verified'], bool)
                or manifest['whole_camera_domain_verified'] is not capture['whole_camera_domain_verified']):
            raise ValueError('Missing or inconsistent coverage declaration')
        size = metadata['size']
        image = cv2.imread(str(self.path / 'map.png'))
        if image is None or list(image.shape[1::-1]) != size:
            raise ValueError('Map image size mismatch')
        validate_annotations(annotations, digest, size)
        crop = metadata['crop']
        left, top, right, bottom = crop['box']
        if (not 0 <= left < right <= crop['source_size'][0]
                or not 0 <= top < bottom <= crop['source_size'][1]
                or [right - left, bottom - top] != size or crop != capture['crop']):
            raise ValueError('Invalid map crop')
        with np.load(self.path / 'source/map_data.npz', allow_pickle=False) as data:
            self.projection = matrix(data['projection'])
            self.warp_size = tuple(int(v) for v in data['warp_size'])
            self.terrain = data['terrain_probability'].copy()
            for name in ('origin', 'positions', 'scan_to_map'):
                cached = np.asarray(data[name], float)
                recorded = np.asarray(metadata['transforms'][name], float)
                if (cached.shape != recorded.shape or not np.isfinite(cached).all()
                        or not np.allclose(cached, recorded)):
                    raise ValueError(f'Map cache transform mismatch: {name}')
        if (not np.allclose(self.projection, matrix(metadata['transforms']['projection']))
                or list(self.warp_size) != metadata['transforms']['warp_size']
                or min(self.warp_size) <= 0 or self.terrain.shape != (size[1], size[0])
                or not np.isfinite(self.terrain).all() or self.terrain.min() < 0):
            raise ValueError('Map cache does not match projection, crop or dimensions')
        # 道路与覆盖权重分别插值后相除，边缘比值可能超过一，消费时限制为有效概率。
        self.terrain = np.minimum(self.terrain, 1)
        if annotations.get('terrain_edits'):
            from .edited_map import edited_roads
            _, override, _ = edited_roads(self.path, metadata, self.terrain >= .5)
            self.terrain[override == 1] = 0
            self.terrain[override == 2] = 1
        calibration = json.loads((self.path / 'calibration.json').read_text(encoding='utf-8'))
        if calibration.get('client') != manifest['client'] or calibration.get('roi') != manifest['roi']:
            raise ValueError('Calibration capture geometry differs from package')
        self.old_projection = matrix(calibration['projection'])
        self.field_inverse = matrix(calibration['field_inverse'], (2, 2))
        self.targets = {}
        for obj in annotations['objects']:
            if obj['type'] != 'point':
                continue
            if obj.get('source', {}).get('difficulty') != difficulty:
                continue
            number = obj.get('source', {}).get('number')
            if number is None:
                continue
            point = np.asarray(obj['points'][0], float)
            if point.shape != (2,) or not np.isfinite(point).all() or np.any(point < 0) or np.any(point >= size):
                raise ValueError('Annotation outside map')
            number = int(number)
            if number in self.targets:
                raise ValueError('Duplicate target number')
            self.targets[number] = point
        self.binding = {'chapter': chapter, 'difficulty': difficulty, 'image_sha256': digest,
                        'package_sha256': hashlib.sha256((self.path / 'validation.json').read_bytes()).hexdigest(),
                        'coverage': manifest['coverage'],
                        'whole_camera_domain_verified': capture['whole_camera_domain_verified']}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    settings.arguments(parser)
    settings.configure(parser.parse_args())
    print(json.dumps(MapPackage().binding, indent=2))
