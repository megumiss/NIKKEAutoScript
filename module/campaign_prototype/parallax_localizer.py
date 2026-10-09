"""Wiki 和现场小队共用原始帧局部配准，沿用分层重绘的 ROI→map 变换。"""

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from dev_tools.minimap_reconstruct import terrain
from .perception import detect_markers
from .edited_map import edited_roads, road_distance
from .local_projection import frame_plane_to_map
from .surface_motion import contains, fit_registration, project


class ParallaxLocalizer:
    def __init__(self, package):
        """加载 local_parallax 包并核对底图和原始帧哈希，读取各帧局部高度标签及道路特征。

        修订道路和几何共同形成缓存身份；只建立离线配准数据，不创建游戏窗口。
        """
        self.path = Path(package)
        self.metadata = meta = json.loads((self.path / 'map.json').read_text(encoding='utf-8'))
        self.digest = hashlib.sha256((self.path / 'map.png').read_bytes()).hexdigest()
        if meta.get('coordinate_model') != 'local_parallax' or self.digest != meta['image_sha256']:
            raise ValueError('Expected a hash-bound local-parallax map')
        self.road, self.override, edits_digest = edited_roads(self.path, meta)
        self.edits_digest = edits_digest
        self.cache_digest = hashlib.sha256((self.path / 'map.json').read_bytes() + edits_digest.encode()).hexdigest()
        scan = json.loads((self.path / 'source/scan.json').read_text(encoding='utf-8'))
        self.sift = cv2.SIFT_create(nfeatures=2000, contrastThreshold=.01, edgeThreshold=12)
        self.references = []
        for index, frame in enumerate(scan['frames']):
            name = frame['file']
            if Path(name).name != name:
                raise ValueError('Invalid source path')
            raw = (self.path / 'source' / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != meta['source_sha256'][name]:
                raise ValueError('Source frame hash mismatch')
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            with np.load(self.path / 'depth' / f'frame_{index:05d}.npz', allow_pickle=False) as data:
                labels = data['local_surface'].copy()
            road = terrain(image)
            valid = cv2.dilate(road, np.ones((7, 7), np.uint8))
            valid[:15] = valid[-15:] = valid[:, :15] = valid[:, -15:] = 0
            valid[-50:, -90:] = 0
            keys, descriptors = self.sift.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), valid)
            points = np.asarray([key.pt for key in keys])
            self.references.append(dict(frame=index, image=image, road=road, labels=labels,
                                        points=points, descriptors=descriptors))
        self.camera = np.asarray(meta['camera'])

    def allows_position(self, point):
        """检查地图点距修订道路不超过 10px，并且未落入人工擦除掩码。

        非法或越界点经 road_distance 判为不可用；返回布尔值，不证明目标与小队所在道路连通。
        """
        if road_distance(self.road, point) > 10:
            return False
        x, y = np.floor(point).astype(int)
        return self.override[y, x] != 1

    def road_proposals(self, image, point, valid):
        """整图道路只提供搜索种子；最终位置由原始帧的局部平面和道路互验决定。

        纹理匹配不足时通过整图道路候选确定搜索区域，再对原始帧局部平面进行验证。
        返回局部候选及粗匹配信息；粗相关峰只是种子，最终接受还需要相机跨度和道路一致性。
        """
        from dev_tools.wiki_collectible_match import MapMatcher, SCALE, PAD
        seed_matcher = MapMatcher.__new__(MapMatcher)
        seed_matcher.surface_matcher = None
        seed_matcher.digest = self.digest
        rotation = np.eye(3)
        rotation[:2, :2] = self.metadata['rotation']
        seed_matcher.projection = rotation @ np.asarray(self.metadata['projection'])
        seed_matcher.terrain = self.road.astype(np.float32)
        seed_matcher.target = cv2.copyMakeBorder(cv2.resize(seed_matcher.terrain, None, fx=SCALE, fy=SCALE,
                                                           interpolation=cv2.INTER_AREA),
                                                 PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT)
        seed_matcher.pad = 900
        seed_matcher.large = cv2.copyMakeBorder(seed_matcher.terrain, 900, 900, 900, 900, cv2.BORDER_CONSTANT)
        seed = seed_matcher.match_panel(image, terrain(image), valid, point)
        seed_matrix = np.asarray(seed['roi_to_map'])
        proposals = []
        query_road = (terrain(image) > 0).astype(np.float32)
        for ref in self.references:
            frame = ref['frame']
            for label, plane in enumerate(self.metadata['frame_support'][frame]['local_planes']):
                if plane is None:
                    continue
                original_to_map = frame_plane_to_map(self.metadata, frame, plane)
                original_point = project(np.linalg.inv(original_to_map), seed['position'])
                local = (ref['labels'] == label).astype(np.uint8)
                if road_distance(local, original_point) > 12:
                    continue
                warp = np.linalg.inv(seed_matrix) @ original_to_map
                surface = cv2.warpPerspective(local, warp, image.shape[1::-1], flags=cv2.INTER_NEAREST)
                common = ((surface > 0) & (valid > 0)).astype(np.uint8)
                if common.sum() < 1500:
                    continue
                recovered = cv2.warpPerspective(ref['road'].astype(np.float32) / 255, warp, image.shape[1::-1])
                try:
                    _, correction = cv2.findTransformECC(query_road, recovered, np.eye(3, dtype=np.float32),
                        cv2.MOTION_HOMOGRAPHY, (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 80, 1e-5),
                        common, 5)
                    matrix = np.linalg.inv(warp) @ correction
                    source_point = project(matrix, point)
                    if road_distance(local, source_point) > 8:
                        continue
                    roi_to_map = original_to_map @ matrix
                    position = project(roi_to_map, point)
                    if not self.allows_position(position):
                        continue
                    recovered = cv2.warpPerspective(ref['road'], np.linalg.inv(matrix), image.shape[1::-1]) > 127
                    surface = cv2.warpPerspective(local, np.linalg.inv(matrix), image.shape[1::-1],
                                                  flags=cv2.INTER_NEAREST) > 0
                    common = surface & (valid > 0)
                    observed = query_road > 0
                    union = np.count_nonzero(common & (observed | recovered))
                    iou = float(np.count_nonzero(common & observed & recovered) / max(1, union))
                    # 分区轮廓验证避免用大面积道路内部的一致掩盖边缘错位。
                    edges = [cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_GRADIENT,
                                             np.ones((3, 3), np.uint8)) > 0 for mask in [observed, recovered]]
                    interior = cv2.erode(common.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
                    distances = []
                    for first, second in [edges, edges[::-1]]:
                        selected = first & interior
                        if selected.sum() < 40:
                            break
                        distances.append(float(np.percentile(cv2.distanceTransform(
                            (~second).astype(np.uint8), cv2.DIST_L2, 5)[selected], 90)))
                    if union < 800 or iou < .9 or len(distances) != 2 or max(distances) > 2:
                        continue
                    proposals.append(dict(reference_frame=frame, local_surface=label, position=position.tolist(),
                        roi_to_map=roi_to_map.tolist(), road_iou=iou, source_point=source_point.tolist(),
                        boundary_p90_px=max(distances), registration_method='source_road_ecc',
                        coarse_margin=seed['coarse_margin']))
                except (cv2.error, ValueError, np.linalg.LinAlgError):
                    continue
        return proposals, seed

    def locate(self, image, point, valid=None, review=None):
        """image 为查询小地图，point 为其中的目标点，valid 可排除遮挡，review 可指定叠图路径。

        按原始帧局部表面配准并投影到地图，返回 accepted 或 needs_review 及候选证据；独立视角不足不强行接受。
        """
        point = np.asarray(point, float)
        if (point.shape != (2,) or not np.isfinite(point).all() or np.any(point < 0)
                or np.any(point >= image.shape[1::-1])):
            raise ValueError('Marker outside minimap')
        valid = np.full(image.shape[:2], 255, np.uint8) if valid is None else valid.copy()
        cv2.circle(valid, tuple(np.rint(point).astype(int)), max(12, round(image.shape[1] * .045)), 0, -1)
        keys, descriptors = self.sift.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), valid)
        if descriptors is None or len(descriptors) < 8:
            raise ValueError('Insufficient minimap features')
        query = np.asarray([key.pt for key in keys])
        matcher, views = cv2.BFMatcher(), []
        for ref in self.references:
            features = ref['descriptors']
            if features is None or len(features) < 2:
                continue
            reverse = matcher.match(features, descriptors)
            matches = [a for a, b in matcher.knnMatch(descriptors, features, k=2)
                       if a.distance < .7 * b.distance and reverse[a.trainIdx].trainIdx == a.queryIdx]
            views.append((len(matches), ref, matches))
        proposals, rejections = [], []
        observed = terrain(image) > 0
        for _, ref, matches in sorted(views, key=lambda item: item[0], reverse=True)[:16]:
            frame = ref['frame']
            for label, plane in enumerate(self.metadata['frame_support'][frame]['local_planes']):
                if plane is None:
                    continue
                pairs = []
                for match in matches:
                    target = ref['points'][match.trainIdx]
                    x, y = np.floor(target).astype(int)
                    if ref['labels'][y, x] == label:
                        pairs.append((query[match.queryIdx], target))
                if len(pairs) < 8:
                    continue
                try:
                    pairs = np.asarray(pairs)
                    registration = fit_registration(pairs[:, 0], pairs[:, 1])
                    if not contains(registration['support'], point):
                        raise ValueError('Marker outside checked feature support')
                    matrix = np.asarray(registration['matrix'])
                    original = project(matrix, point)
                    local_domain = (ref['labels'] == label).astype(np.uint8)
                    if road_distance(local_domain, original) > 8:
                        raise ValueError('Marker has no source surface')
                    roi_to_map = frame_plane_to_map(self.metadata, frame, plane) @ matrix
                    position = project(roi_to_map, point)
                    if not self.allows_position(position):
                        raise ValueError('Marker outside edited roads')
                    recovered = cv2.warpPerspective(ref['road'], np.linalg.inv(matrix), image.shape[1::-1]) > 127
                    domain = np.zeros(valid.shape, np.uint8)
                    cv2.fillConvexPoly(domain, np.rint(registration['support']).astype(np.int32), 1)
                    surface = cv2.warpPerspective(local_domain, np.linalg.inv(matrix), image.shape[1::-1],
                                                  flags=cv2.INTER_NEAREST) > 0
                    common = (domain > 0) & (valid > 0) & surface
                    union = int(np.count_nonzero(common & (observed | recovered)))
                    iou = float(np.count_nonzero(common & observed & recovered) / max(1, union))
                    if union < 300 or iou < .85:
                        raise ValueError('Local road overlap insufficient')
                    proposals.append(dict(reference_frame=frame, local_surface=label, position=position.tolist(),
                                          roi_to_map=roi_to_map.tolist(), road_iou=iou, registration=registration,
                                          source_point=original.tolist()))
                except ValueError as exc:
                    rejections.append(dict(frame=frame, label=label, reason=str(exc)))
        seed = None
        if len({p['reference_frame'] for p in proposals}) < 2:
            road_candidates, seed = self.road_proposals(image, point, valid)
            proposals.extend(road_candidates)
        neighborhood = np.zeros(valid.shape, np.uint8)
        cv2.circle(neighborhood, tuple(np.rint(point).astype(int)), max(45, round(min(valid.shape) * .15)), 1, -1)
        common = (neighborhood > 0) & (valid > 0)
        selected = []
        for candidate in proposals:
            recovered = cv2.warpPerspective(self.road, np.linalg.inv(candidate['roi_to_map']), image.shape[1::-1],
                                            flags=cv2.INTER_NEAREST) > 0
            union = np.count_nonzero(common & (observed | recovered))
            candidate['edited_road_iou'] = float(np.count_nonzero(common & observed & recovered) / max(1, union))
            if union >= 300 and candidate['edited_road_iou'] >= .85:
                selected.append(candidate)
            else:
                rejections.append(dict(frame=candidate['reference_frame'], label=candidate['local_surface'],
                                       reason='Position disagrees with edited road shape',
                                       edited_road_iou=candidate['edited_road_iou']))
        if proposals and max(np.linalg.norm(np.asarray(p['position']) - proposals[0]['position'])
                             for p in proposals) > 8:
            proposals = selected
        result = dict(status='needs_review', image_sha256=self.digest, coordinate_model='local_parallax',
                      player_center=point.tolist(), candidates=proposals, rejected_surfaces=rejections,
                      matched_features=max((item[0] for item in views), default=0), edits_sha256=self.edits_digest)
        if proposals:
            best = max(proposals, key=lambda item: item['road_iou'])
            spread = max(float(np.linalg.norm(np.asarray(p['position']) - best['position'])) for p in proposals)
            independent = any(np.linalg.norm(self.camera[p['reference_frame']] -
                                             self.camera[best['reference_frame']]) >= 40 for p in proposals)
            result.update(best, parameter_spread_px=spread)
            textured_views = {p['reference_frame'] for p in proposals if 'registration' in p}
            if spread <= 8 and independent and (len(textured_views) >= 2 or seed['coarse_margin'] >= .1):
                result['status'] = 'accepted'
            else:
                result['reason'] = 'Missing independent view or ambiguous map position'
        else:
            result['reason'] = 'No locally verified source correspondence'
        if review is not None:
            overlay = image.copy()
            if proposals:
                recovered = cv2.warpPerspective(self.road, np.linalg.inv(result['roi_to_map']), image.shape[1::-1])
                contours, _ = cv2.findContours(recovered, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(overlay, contours, -1, (0, 255, 255), 1)
            cv2.drawMarker(overlay, tuple(np.rint(point).astype(int)), (0, 215, 255), cv2.MARKER_CROSS, 14, 1)
            if not cv2.imwrite(str(review), np.hstack([image, overlay])):
                raise OSError('Cannot save localization review')
        return result

    def locate_squad(self, image, review=None):
        """先在输入小地图中识别唯一小队圆环，再复用 locate 的局部表面验证。

        结果补齐 position_kind、player_roi 和 iou 供导航消费；零个或多个圆环直接抛错。
        """
        players, _ = detect_markers(image, np.eye(3))
        if len(players) != 1:
            raise ValueError('Expected exactly one squad ring')
        result = self.locate(image, players[0], review=review)
        result.update(position_kind='squad', player_roi=np.asarray(players[0]).tolist(), iou=result.get('road_iou', 0))
        return result
