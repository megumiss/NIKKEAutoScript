"""分层包验收度量：源帧哈希、远隔重访轮廓一致性、深度支持与区域重绘统计；只读取包，不改写。

python -m dev_tools.minimap_verify --package <local_parallax 包目录> [--output <报告 JSON>] [--pairs 40]
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np

from dev_tools.minimap_layered import project
from dev_tools.minimap_reconstruct import terrain

MARGIN = 64
SHIFT_RANGE = 32


def frame_valid(shape):
    """与重建阶段相同地排除边框和右下角控件，道路掩码由调用者另行叠加。"""
    valid = np.ones(shape, np.uint8)
    valid[:15] = valid[-15:] = 0
    valid[:, :15] = valid[:, -15:] = 0
    valid[-50:, -90:] = 0
    return valid


def summary(values):
    """统计全部样本，不筛选有利子集；没有样本时各项为 None。"""
    values = np.asarray(values, float)
    if not len(values):
        return {'samples': 0, 'median_px': None, 'p90_px': None, 'p95_px': None, 'within_3px': None}
    return {'samples': int(len(values)), 'median_px': float(np.median(values)),
            'p90_px': float(np.percentile(values, 90)), 'p95_px': float(np.percentile(values, 95)),
            'within_3px': float(np.mean(values <= 3))}


class LayeredPackage:
    def __init__(self, package):
        """读取 local_parallax 包的元数据、原始帧与逐帧规整深度；哈希不符只记录，不中止度量。"""
        self.path = Path(package).resolve()
        self.meta = json.loads((self.path / 'map.json').read_text(encoding='utf-8'))
        if self.meta.get('coordinate_model') != 'local_parallax':
            raise ValueError('Expected a local_parallax package')
        scan = json.loads((self.path / 'source/scan.json').read_text(encoding='utf-8'))
        self.images, self.hash_mismatches = [], []
        for frame in scan['frames']:
            name = frame['file']
            if Path(name).name != name:
                raise ValueError('Invalid source frame path')
            raw = (self.path / 'source' / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != self.meta['source_sha256'].get(name):
                self.hash_mismatches.append(name)
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f'Unreadable source frame: {name}')
            self.images.append(image)
        self.roads = [terrain(image) for image in self.images]
        self.valid = frame_valid(self.images[0].shape[:2])
        self.ratios = []
        for index in range(len(self.images)):
            with np.load(self.path / 'depth' / f'frame_{index:05d}.npz', allow_pickle=False) as data:
                self.ratios.append(data['ratio'])
        self.camera = np.asarray(self.meta['camera'], float)
        self.vertical = np.asarray(self.meta['vertical_origin'], float)
        self.rotation = np.asarray(self.meta['rotation'], float)
        self.origin = np.asarray(self.meta['origin'], float)
        self.projection = np.asarray(self.meta['projection'], float)
        self.size = tuple(int(value) for value in self.meta['size'])
        self.image_sha256 = hashlib.sha256((self.path / 'map.png').read_bytes()).hexdigest()
        self._fields = {}
        self._contours = {}

    def to_map(self, frame, points, ratios):
        """沿用重建公式把 ROI 点按各自比例投到地图像素，比例必须来自对应表面。"""
        plane = project(self.projection, points)
        world = self.vertical + (plane - self.vertical) / np.asarray(ratios, float)[:, None] + self.camera[frame]
        return world @ self.rotation.T - self.origin

    def road_mask(self, frame, shared_ratio):
        """有效区域内的道路；逐像素模式下再要求有规整深度。"""
        road = (self.roads[frame] > 0) & (self.valid > 0)
        if shared_ratio is None:
            road &= np.isfinite(self.ratios[frame])
        return road

    def distance_field(self, frame, shared_ratio):
        """把一帧道路投到带边距的地图画布，返回到最近投影道路像素的距离场；无深度时返回 None。"""
        key = (frame, shared_ratio)
        if key not in self._fields:
            ys, xs = np.nonzero(self.road_mask(frame, shared_ratio))
            if not len(xs):
                self._fields[key] = None
                return None
            ratios = np.full(len(xs), shared_ratio, float) if shared_ratio is not None else self.ratios[frame][ys, xs]
            mapped = self.to_map(frame, np.column_stack([xs, ys]).astype(float), ratios) + MARGIN
            width, height = self.size[0] + 2 * MARGIN, self.size[1] + 2 * MARGIN
            inside = ((mapped[:, 0] >= 0) & (mapped[:, 0] < width - 1)
                      & (mapped[:, 1] >= 0) & (mapped[:, 1] < height - 1))
            canvas = np.zeros((height, width), np.uint8)
            rounded = np.rint(mapped[inside]).astype(int)
            canvas[rounded[:, 1], rounded[:, 0]] = 1
            # 投影散点之间留有亚像素缝隙，先膨胀并闭运算，再取轮廓。
            canvas = cv2.dilate(canvas, np.ones((3, 3), np.uint8))
            canvas = cv2.morphologyEx(canvas, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
            # 距离场基于投影道路的轮廓而不是填充区域，否则把轮廓平移进道路内部也会得到零距离。
            edge = cv2.morphologyEx(canvas, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
            self._fields[key] = cv2.distanceTransform(1 - edge, cv2.DIST_L2, 5)
        return self._fields[key]

    def mapped_contour(self, frame, shared_ratio):
        """帧内道路轮廓点投到带边距的地图坐标；轮廓取自原始道路，再剔除没有深度的点。

        轮廓不取自有深度的区域，否则深度缺失的边界会被当成道路边缘计分。
        """
        key = (frame, shared_ratio)
        if key not in self._contours:
            road = ((self.roads[frame] > 0) & (self.valid > 0)).astype(np.uint8)
            contours, _ = cv2.findContours(road, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            mapped = np.zeros((0, 2))
            if contours:
                points = np.concatenate([contour[:, 0] for contour in contours])[::2]
                xs, ys = points.T
                ratios = (np.full(len(points), shared_ratio, float) if shared_ratio is not None
                          else self.ratios[frame][ys, xs])
                keep = np.isfinite(ratios)
                if keep.any():
                    mapped = self.to_map(frame, points[keep].astype(float), ratios[keep]) + MARGIN
            self._contours[key] = mapped
        return self._contours[key]

    @staticmethod
    def sample(field, points):
        """读取落在画布内的点的距离值，画布外的点不计入。"""
        height, width = field.shape
        inside = ((points[:, 0] >= 0) & (points[:, 0] < width - 1)
                  & (points[:, 1] >= 0) & (points[:, 1] < height - 1))
        rounded = np.rint(points[inside]).astype(int)
        return field[rounded[:, 1], rounded[:, 0]]

    def contour_distances(self, source, target, shared_ratio, shift=(0., 0.)):
        """source 帧轮廓投影后到 target 帧投影道路的距离；shift 只用于整体平移诊断。"""
        field = self.distance_field(target, shared_ratio)
        points = self.mapped_contour(source, shared_ratio)
        if field is None or not len(points):
            return np.zeros(0)
        return self.sample(field, points + np.asarray(shift, float))

    def best_shift(self, source, target, shared_ratio):
        """在 ±SHIFT_RANGE 像素内由粗到细搜索使轮廓距离中位数最小的整体平移。

        该平移只用来区分不一致来自整体位姿漂移还是局部深度错误，不参与任何坐标修正。
        """
        field = self.distance_field(target, shared_ratio)
        points = self.mapped_contour(source, shared_ratio)
        if field is None or len(points) < 20:
            return np.zeros(2), np.zeros(0)
        best, center = None, np.zeros(2)
        for step, radius in ((4, SHIFT_RANGE), (1, 4)):
            offsets = np.arange(-radius, radius + 1, step)
            for dy in offsets:
                for dx in offsets:
                    shift = center + [dx, dy]
                    values = self.sample(field, points + shift)
                    if len(values) < 20:
                        continue
                    score = float(np.median(values))
                    if best is None or score < best[0]:
                        best = (score, shift)
            if best is None:
                return np.zeros(2), np.zeros(0)
            center = best[1]
        return best[1], self.sample(field, points + best[1])


def revisit_pairs(camera, max_distance=60., min_gap=15, limit=40, spacing=50.):
    """挑选相机位置接近但采集顺序相隔较远的帧对，并按区域分散，避免同一处重复计分。"""
    count = len(camera)
    candidates = []
    for first in range(count):
        for second in range(first + min_gap, count):
            distance = float(np.linalg.norm(camera[first] - camera[second]))
            if distance <= max_distance:
                candidates.append((second - first, distance, first, second))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    chosen, centers = [], []
    for gap, distance, first, second in candidates:
        center = (camera[first] + camera[second]) / 2
        if all(np.linalg.norm(center - existing) >= spacing for existing in centers):
            chosen.append((first, second, distance, gap))
            centers.append(center)
        if len(chosen) >= limit:
            break
    return chosen


def verify(package_dir, pairs=40):
    """生成一份分层包度量报告；所有数值都是包内一致性证据，不是独立几何或导航验收。"""
    package = LayeredPackage(package_dir)
    meta = package.meta
    finite = np.concatenate([ratio[np.isfinite(ratio)] for ratio in package.ratios])
    shared = float(np.median(finite)) if len(finite) else 1.
    report = dict(package=str(package.path), chapter=meta.get('chapter'), image_sha256=package.image_sha256,
                  image_sha256_matches=package.image_sha256 == meta.get('image_sha256'),
                  checked_at=time.strftime('%Y-%m-%dT%H:%M:%S'), frames=len(package.images),
                  source_hash_mismatches=package.hash_mismatches, map_render_mode=meta.get('map_render_mode'),
                  track_validation=meta.get('validation'), rejected_observations=meta.get('rejected_observations'),
                  supported_observation_fraction=meta.get('supported_observation_fraction'), shared_ratio=shared)
    road_total, supported, planes, empty = 0, 0, [], 0
    for index in range(len(package.images)):
        road = (package.roads[index] > 0) & (package.valid > 0)
        has_depth = np.isfinite(package.ratios[index])
        road_total += int(road.sum())
        supported += int((road & has_depth).sum())
        empty += int(not has_depth.any())
        planes.append(sum(plane is not None for plane in meta['frame_support'][index]['local_planes']))
    report['depth_support'] = dict(road_pixels=road_total, supported_road_pixels=supported,
                                   supported_fraction=supported / max(1, road_total), frames_without_depth=empty,
                                   mean_planes_per_frame=float(np.mean(planes)) if planes else 0.)
    selected = revisit_pairs(package.camera, limit=pairs)
    details = []
    pooled = {key: dict(raw=[], aligned=[], shifts=[]) for key in ('local', 'shared')}
    for first, second, distance, gap in selected:
        entry = dict(frames=[first, second], camera_distance_px=distance, index_gap=gap)
        for key, ratio in (('local', None), ('shared', shared)):
            raw = np.concatenate([package.contour_distances(first, second, ratio),
                                  package.contour_distances(second, first, ratio)])
            shift, forward = package.best_shift(first, second, ratio)
            aligned = np.concatenate([forward, package.contour_distances(second, first, ratio, shift=-shift)])
            entry[key] = dict(summary(raw), shift_px=[float(value) for value in shift],
                              shift_norm_px=float(np.linalg.norm(shift)), aligned=summary(aligned))
            pooled[key]['raw'].append(raw)
            pooled[key]['aligned'].append(aligned)
            pooled[key]['shifts'].append(float(np.linalg.norm(shift)))
        details.append(entry)
    revisit = dict(pairs=len(details), max_camera_distance_px=60, min_index_gap=15, shift_search_px=SHIFT_RANGE,
                   details=details)
    for key, bucket in pooled.items():
        raw = np.concatenate(bucket['raw']) if bucket['raw'] else np.zeros(0)
        revisit[key] = summary(raw)
        medians = [entry[key]['median_px'] for entry in details if entry[key]['median_px'] is not None]
        revisit[key]['median_of_pair_medians_px'] = float(np.median(medians)) if medians else None
        revisit[key]['aligned'] = summary(np.concatenate(bucket['aligned']) if bucket['aligned'] else np.zeros(0))
        revisit[key]['median_shift_norm_px'] = float(np.median(bucket['shifts'])) if bucket['shifts'] else None
    report['revisit_contour_distance'] = revisit
    redraw = meta.get('projected_redraw')
    if redraw:
        keys = ('method', 'attempted_regions', 'accepted_regions', 'active_regions', 'painted_pixels',
                'changed_pixels', 'rejection_events')
        report['redraw'] = {key: redraw.get(key) for key in keys}
        report['redraw']['painted_fraction_of_canvas'] = redraw.get('painted_pixels', 0) / float(
            package.size[0] * package.size[1])
    report['limitations'] = [
        'Revisit pairs reuse the fitted camera and depth; they measure internal consistency, not ground truth.',
        'The shared variant projects every pixel with one median ratio and only shows what a single plane would do.',
        'Aligned residuals come from a diagnostic translation search; they are not corrected coordinates.',
        'Depth support and redraw counts come from the package itself and do not prove navigation readiness.']
    return report


def main():
    """只读取指定包并写出报告，默认放在 log/minimap_verify/ 下，不触碰包内文件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--pairs', type=int, default=40)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    report = verify(args.package, args.pairs)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    output = args.output or Path('log/minimap_verify') / f"chapter_{report['chapter']:02d}_{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    revisit = report['revisit_contour_distance']
    brief = {key: {name: revisit[key][name] for name in ('median_px', 'p90_px', 'within_3px', 'median_shift_norm_px')}
             for key in ('local', 'shared')}
    for key in ('local', 'shared'):
        brief[key]['aligned_p90_px'] = revisit[key]['aligned']['p90_px']
        brief[key]['aligned_within_3px'] = revisit[key]['aligned']['within_3px']
    print(json.dumps({'chapter': report['chapter'], 'frames': report['frames'],
                      'supported_fraction': round(report['depth_support']['supported_fraction'], 4),
                      'revisit_pairs': revisit['pairs'], **brief,
                      'redraw': {k: report.get('redraw', {}).get(k) for k in ('attempted_regions', 'accepted_regions')},
                      'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
