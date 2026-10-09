"""Wiki 与小队共用的局部表面定位；几何未验收时仅输出待复核坐标。"""

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from dev_tools.minimap_reconstruct import terrain
from .perception import detect_markers
from .surface_geometry import SurfaceGeometry
from .surface_motion import contains, fit_registration, project


class SurfaceLocalizer:
    def __init__(self, package):
        """核对地图与分层缓存，参考特征只来自参与拼图的帧。

        加载 orthographic_surfaces 地图及对应原始缓存，验证底图哈希并建立表面几何。
        参考特征仅从参与拼图的帧提取，避免把留出帧混入匹配证据；格式或绑定错误直接拒绝。
        """
        self.path = Path(package)
        self.metadata = json.loads((self.path / 'map.json').read_text(encoding='utf-8'))
        meta = self.metadata
        if meta.get('coordinate_model') != 'orthographic_surfaces':
            raise ValueError('Expected an orthographic surface map')
        self.digest = hashlib.sha256((self.path / 'map.png').read_bytes()).hexdigest()
        self.cache_digest = hashlib.sha256((self.path / meta['geometry_data']).read_bytes()).hexdigest()
        if meta['image_sha256'] != self.digest or meta['geometry_sha256'] != self.cache_digest:
            raise ValueError('Surface map or geometry hash mismatch')
        self.geometry = SurfaceGeometry(meta['projection'], meta['vertical_origin'], meta['camera'],
                                        {surface['id']: surface['height_plane'] for surface in meta['surfaces']},
                                        meta['world_to_map'])
        with np.load(self.path / meta['geometry_data'], allow_pickle=False) as data:
            self.masks = data['masks'].copy()
            labels = data['roi_surface_id'].copy()
        if (self.masks.shape != (len(meta['surfaces']), *meta['size'][::-1])
                or len(labels) != len(meta['camera'])):
            raise ValueError('Surface raster dimensions differ from map metadata')
        self.identifiers = [surface['id'] for surface in meta['surfaces']]
        scan = json.loads((self.path / 'source/scan.json').read_text(encoding='utf-8'))
        self.sift = cv2.SIFT_create(nfeatures=1300, contrastThreshold=.012, edgeThreshold=8)
        self.references = []
        for frame in meta['texture_frames']:
            name = scan['frames'][frame]['file']
            path = self.path / 'source' / name
            if Path(name).name != name or hashlib.sha256(path.read_bytes()).hexdigest() != meta['source_sha256'][name]:
                raise ValueError('Reference frame differs from fitted source')
            image = cv2.imread(str(path))
            road = cv2.dilate(terrain(image), np.ones((7, 7), np.uint8))
            valid = ((road > 0) & (labels[frame] >= 0)).astype(np.uint8) * 255
            points, descriptors = self.sift.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), valid)
            if descriptors is None:
                continue
            points = np.asarray([point.pt for point in points])
            pixels = np.floor(points).astype(int)
            identity = labels[frame, pixels[:, 1], pixels[:, 0]]
            self.references.append((frame, points, identity, descriptors))

    def locate(self, image, point, valid=None, review=None):
        """按各表面分别配准，同一目标落入多个候选时保留歧义，不套整图单应。

        对查询图中的 point 搜索各表面局部对应，valid 用于屏蔽不可用像素，review 可保存诊断叠图。
        返回候选位置、表面身份和质量状态；存在多层歧义或几何未验收时保留 needs_review，不能据此驱动移动。
        """
        if image is None or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError('Expected a BGR minimap image')
        point = np.asarray(point, float)
        if (point.shape != (2,) or not np.isfinite(point).all() or np.any(point < 0)
                or np.any(point >= image.shape[1::-1])):
            raise ValueError('Target marker lies outside minimap')
        valid = np.ones(image.shape[:2], np.uint8) * 255 if valid is None else valid.copy()
        cv2.circle(valid, tuple(np.rint(point).astype(int)), max(12, round(image.shape[1] * .045)), 0, -1)
        keys, descriptors = self.sift.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), valid)
        if descriptors is None or len(descriptors) < 8:
            raise ValueError('Insufficient surface features')
        current = np.asarray([key.pt for key in keys])
        matcher, views = cv2.BFMatcher(), []
        for frame, points, identities, reference in self.references:
            if len(reference) < 2:
                continue
            reverse = matcher.match(reference, descriptors)
            matches = [a for a, b in matcher.knnMatch(descriptors, reference, k=2)
                       if a.distance < b.distance * .65 and reverse[a.trainIdx].trainIdx == a.queryIdx]
            views.append((len(matches), frame, points, identities, matches))
        groups = []
        for _, frame, points, identities, matches in sorted(views, key=lambda view: view[0], reverse=True)[:8]:
            frame_groups = [[] for _ in self.identifiers]
            for match in matches:
                label = int(identities[match.trainIdx])
                if label < 0:
                    continue
                try:
                    mapped = self.geometry.map_point(frame, self.identifiers[label], points[match.trainIdx])
                except ValueError:
                    continue
                frame_groups[label].append((current[match.queryIdx], mapped))
            groups.extend((frame, label, pairs) for label, pairs in enumerate(frame_groups) if pairs)
        proposals, rejected = [], []
        for frame, label, pairs in groups:
            if len(pairs) < 8:
                continue
            pairs = np.asarray(pairs)
            try:
                registration = fit_registration(pairs[:, 0], pairs[:, 1])
            except ValueError as exc:
                rejected.append({'frame': frame, 'surface_id': self.identifiers[label], 'reason': str(exc)})
                continue
            if not contains(registration['support'], point):
                rejected.append({'frame': frame, 'surface_id': self.identifiers[label],
                                 'reason': 'Target outside independently checked feature support'})
                continue
            matrix = np.asarray(registration['matrix'])
            position = project(matrix, point)
            if np.any(position < 0) or np.any(position >= self.metadata['size']):
                continue
            x, y = np.rint(position).astype(int)
            if x >= self.masks.shape[2] or y >= self.masks.shape[1] or not self.masks[label, y, x]:
                continue
            recovered = cv2.warpPerspective(self.masks[label].astype(np.uint8), np.linalg.inv(matrix),
                                            image.shape[1::-1], flags=cv2.INTER_NEAREST) > 0
            domain = np.zeros(valid.shape, np.uint8)
            cv2.fillConvexPoly(domain, np.rint(registration['support']).astype(np.int32), 1)
            common = (domain > 0) & (valid > 0)
            observed = terrain(image) > 0
            union = np.count_nonzero(common & (observed | recovered))
            iou = float(np.count_nonzero(common & observed & recovered) / max(1, union))
            proposals.append(dict(surface_id=self.identifiers[label], reference_frame=frame, position=position.tolist(),
                                  roi_to_map=matrix.tolist(), road_iou=iou, registration=registration))
        result = {'status': 'needs_review', 'image_sha256': self.digest, 'candidates': proposals,
                  'coordinate_model': 'orthographic_surfaces', 'player_center': point.tolist(),
                  'matched_features': max((view[0] for view in views), default=0), 'rejected_surfaces': rejected}
        qualified = [candidate for candidate in proposals if candidate['road_iou'] >= .85]
        consistent = (len(qualified) >= 2 and len({candidate['surface_id'] for candidate in qualified}) == 1
                      and max(np.linalg.norm(np.asarray(candidate['position']) - qualified[0]['position'])
                              for candidate in qualified) <= 6)
        if consistent:
            result.update(max(qualified, key=lambda candidate: candidate['road_iou']))
            validation = self.metadata.get('validation', {})
            if (validation.get('independent_geometry_verified') is True
                    and validation.get('surface_identity_verified') is True):
                result['status'] = 'accepted'
            else:
                result['reason'] = 'Surface geometry has not passed independent validation'
        else:
            result['reason'] = 'Missing or ambiguous surface correspondence'
        if review is not None:
            overlay = image.copy()
            for candidate in proposals:
                label = self.identifiers.index(candidate['surface_id'])
                recovered = cv2.warpPerspective(self.masks[label].astype(np.uint8),
                                                np.linalg.inv(candidate['roi_to_map']), image.shape[1::-1])
                contours, _ = cv2.findContours(recovered, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(overlay, contours, -1, (0, 255, 255), 1)
            if not cv2.imwrite(str(review), np.hstack([image, overlay])):
                raise OSError('Could not save surface localization review')
        return result

    def locate_squad(self, image, review=None):
        """小队和 Wiki 圆环最终都进入 locate，区别只在截图布局和圆环检测。

        识别唯一小队圆环后交由共享局部定位流程处理。
        返回报告保留 ROI 小队坐标和定位质量；圆环缺失或多解抛错，不使用画面中心代替。
        """
        players, _ = detect_markers(image, np.eye(3))
        if len(players) != 1:
            raise ValueError('Expected exactly one squad ring')
        result = self.locate(image, players[0], review=review)
        result.update(position_kind='squad', player_roi=np.asarray(players[0]).tolist())
        return result
