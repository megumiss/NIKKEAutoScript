"""Register Wiki minimap screenshots against a chapter package; reject uncertain positions."""

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


ROI = (25, 96, 243, 307)
SCALE = 0.25
PAD = 180
MATCH_VERSION = 2


def minimap_masks(image):
    height, width = image.shape[:2]
    if abs(width / height - 16 / 9) > 0.12:
        raise ValueError('Unsupported screenshot layout; retain for manual review.')
    normalized = cv2.resize(image, (1920, 1080))
    x, y, right, bottom = ROI
    panel = normalized[y:bottom, x:right]
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    road = cv2.inRange(hsv, (85, 65, 115), (115, 255, 255))
    # Original PNGs retain thin bright grid lines that CDN-compressed previews blur away.
    road = cv2.morphologyEx(road, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    valid = np.full(road.shape, 255, np.uint8)
    valid[:3] = valid[-3:] = 0
    valid[:, :3] = valid[:, -3:] = 0
    valid[:29, :42] = valid[:29, -34:] = valid[-31:, -76:] = 0
    bright = (hsv[:, :, 2] > 225).astype(np.uint8) * 255
    bright[72:130, 78:138] = 0
    valid[cv2.dilate(bright, np.ones((15, 15), np.uint8)) > 0] = 0
    player = player_center(panel)
    cv2.circle(valid, tuple(np.rint(player).astype(int)), 21, 0, -1)
    road[valid == 0] = 0
    if np.count_nonzero(road) < 200:
        raise ValueError('Too little minimap road evidence.')
    return panel, road, valid, player


def player_center(panel):
    white = cv2.inRange(cv2.cvtColor(panel, cv2.COLOR_BGR2HSV), (0, 0, 145), (179, 90, 255))
    white[:74] = white[134:] = white[:, :78] = white[:, 143:] = 0
    ys, xs = np.nonzero(white)
    points = np.column_stack([xs, ys]).astype(float)
    if len(points) < 25:
        raise ValueError('Squad ring is absent from the expected minimap region.')
    rng = np.random.default_rng(40)
    best = None
    for _ in range(1800):
        q = points[rng.choice(len(points), 3, replace=False)]
        matrix = 2 * (q[1:] - q[0])
        if abs(np.linalg.det(matrix)) < 1:
            continue
        center = np.linalg.solve(matrix, (q[1:] ** 2).sum(axis=1) - (q[0] ** 2).sum())
        radius = np.linalg.norm(q[0] - center)
        if not (9 < radius < 19 and 88 < center[0] < 127 and 85 < center[1] < 124):
            continue
        keep = np.abs(np.linalg.norm(points - center, axis=1) - radius) < 1.4
        angles = np.arctan2((points - center)[keep, 1], (points - center)[keep, 0])
        bins = len(np.unique(np.floor((angles + np.pi) * 16 / (2 * np.pi))))
        score = int(keep.sum()) + bins * 2
        if bins >= 11 and (best is None or score > best[0]):
            best = score, keep
    if best is None:
        raise ValueError('No reliable squad ring; do not use a fixed screenshot center.')
    keep = best[1]
    for _ in range(3):
        q = points[keep]
        if len(q) < 25:
            raise ValueError('Insufficient squad ring support.')
        sol = np.linalg.lstsq(np.column_stack([2 * q, np.ones(len(q))]), (q * q).sum(axis=1), rcond=None)[0]
        center = sol[:2]
        radius = np.sqrt(max(0, sol[2] + (center * center).sum()))
        keep = np.abs(np.linalg.norm(points - center, axis=1) - radius) < 1.4
    if not (9 < radius < 19 and 88 < center[0] < 127 and 85 < center[1] < 124):
        raise ValueError('Refined squad ring is outside expected bounds.')
    return center


def template_matrix(projection, a, b, shape):
    height, width = shape
    transform = np.array([[a, 0, 243 - a * width / 2], [0, a, 231 - a * height / 2], [0, 0, 1.]])
    matrix = np.diag([b * SCALE, b * SCALE, 1.]) @ projection @ transform
    corners = cv2.perspectiveTransform(np.array([[[0., 0], [width, 0], [width, height], [0, height]]]), matrix)[0]
    lower = np.floor(corners.min(axis=0))
    size = np.ceil(corners.max(axis=0) - lower).astype(int)
    if not np.isfinite(corners).all() or min(size) < 8 or max(size) > 600:
        return None
    matrix = np.array([[1, 0, -lower[0]], [0, 1, -lower[1]], [0, 0, 1.]]) @ matrix
    return matrix, tuple(int(v) for v in size)


def iou_surface(target, road, valid, matrix, size):
    if size[0] > target.shape[1] or size[1] > target.shape[0]:
        return None
    r = cv2.warpPerspective(road, matrix, size, flags=cv2.INTER_LINEAR).astype(np.float32) / 255
    v = cv2.warpPerspective(valid, matrix, size, flags=cv2.INTER_NEAREST).astype(np.float32) / 255
    r *= v
    if r.sum() < 30:
        return None
    intersection = cv2.matchTemplate(target, r, cv2.TM_CCORR)
    total = cv2.matchTemplate(target, v, cv2.TM_CCORR)
    return np.clip(intersection / np.maximum(total + r.sum() - intersection, 1), 0, 1)


class MapMatcher:
    def __init__(self, package):
        self.package = Path(package)
        meta = json.loads((self.package / 'map.json').read_text(encoding='utf-8'))
        self.digest = hashlib.sha256((self.package / 'map.png').read_bytes()).hexdigest()
        if meta['image_sha256'] != self.digest:
            raise ValueError('Map hash differs from metadata.')
        if meta.get('capture', {}).get('registration', {}).get('status') != 'joint_grid_road':
            raise ValueError('Map package lacks accepted road registration.')
        self.projection = np.asarray(meta['transforms']['projection'], float)
        cache_path = self.package / 'source/map_data.npz'
        self.cache_digest = hashlib.sha256(cache_path.read_bytes()).hexdigest()
        with np.load(cache_path) as cache:
            self.terrain = (cache['terrain_probability'] >= .5).astype(np.float32)
            if not np.allclose(cache['projection'], self.projection):
                raise ValueError('Cached projection differs from map metadata.')
        if list(self.terrain.shape[::-1]) != meta['size']:
            raise ValueError('Cached terrain dimensions differ from the map.')
        self.target = cv2.copyMakeBorder(cv2.resize(self.terrain, None, fx=SCALE, fy=SCALE,
                                                  interpolation=cv2.INTER_AREA),
                                        PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT, value=0)
        self.pad = 900
        self.large = cv2.copyMakeBorder(self.terrain, self.pad, self.pad, self.pad, self.pad,
                                       cv2.BORDER_CONSTANT, value=0)

    def match(self, image, review=None):
        panel, road, valid, player = minimap_masks(image)
        coarse = []
        parameters = {(round(float(a), 2), round(float(b), 2))
                      for a in np.arange(.6, 2.21, .2) for b in np.arange(.65, 1.56, .15)}
        parameters.update((round(float(a), 2), round(float(b), 2))
                          for a in np.arange(1.9, 3.21, .1) for b in np.arange(.45, .851, .05))
        for a, b in sorted(parameters):
            template = template_matrix(self.projection, a, b, road.shape)
            if template is None:
                continue
            matrix, size = template
            surface = iou_surface(self.target, road, valid, matrix, size)
            if surface is None:
                continue
            _, score, _, location = cv2.minMaxLoc(surface)
            cv2.circle(surface, location, round(130 * SCALE), 0, -1)
            _, second, _, second_location = cv2.minMaxLoc(surface)
            p = cv2.perspectiveTransform(player[None, None], matrix)[0, 0]
            coarse.append({'a': a, 'b': b, 'score': score, 'second': second,
                           'position': (np.asarray(location) + p - PAD) / SCALE,
                           'second_position': (np.asarray(second_location) + p - PAD) / SCALE,
                           'location': location, 'matrix': matrix, 'size': size})
        if not coarse:
            raise ValueError('No usable registration candidates.')
        coarse.sort(key=lambda r: r['score'], reverse=True)
        candidates = []
        for seed in coarse[:8]:
            full = np.diag([1 / SCALE, 1 / SCALE, 1.]) @ seed['matrix']
            size = tuple(round(v / SCALE) for v in seed['size'])
            predicted = np.asarray(seed['location']) / SCALE - PAD / SCALE
            start = np.rint(predicted).astype(int) + self.pad - 20
            if min(start) < 0:
                continue
            patch = self.large[start[1]:start[1] + size[1] + 40, start[0]:start[0] + size[0] + 40]
            surface = iou_surface(patch, road, valid, full, size)
            if surface is None:
                continue
            _, score, _, location = cv2.minMaxLoc(surface)
            offset = start + np.asarray(location) - self.pad
            matrix = np.array([[1, 0, offset[0]], [0, 1, offset[1]], [0, 0, 1.]]) @ full
            point = cv2.perspectiveTransform(player[None, None], matrix)[0, 0]
            candidates.append({'road_iou': score, 'position': point.tolist(), 'roi_to_map': matrix.tolist(),
                               'a': seed['a'], 'b': seed['b']})
        if not candidates:
            raise ValueError('No full-resolution registration candidate fits the map.')
        result = max(candidates, key=lambda r: r['road_iou'])
        point = np.asarray(result['position'])
        alternatives = [r['score'] for r in coarse if np.linalg.norm(r['position'] - point) > 130]
        alternatives += [r['second'] for r in coarse if np.linalg.norm(r['second_position'] - point) > 130]
        margin = coarse[0]['score'] - max(alternatives, default=0)
        spread = max(np.linalg.norm(np.asarray(r['position']) - point) for r in candidates
                     if r['road_iou'] >= result['road_iou'] - .03)
        inside = np.all(point >= 0) and np.all(point <= np.asarray(self.terrain.shape[::-1]) - 1)
        accepted = bool(inside and result['road_iou'] >= .85 and margin >= .10 and spread <= 20)
        result.update(status='accepted' if accepted else 'needs_review', coarse_margin=float(margin),
                      parameter_spread_px=float(spread), roi=list(ROI), player_center=player.tolist(),
                      normalized_size=[1920, 1080], image_sha256=self.digest, match_version=MATCH_VERSION)
        if review is not None:
            recovered = cv2.warpPerspective((self.terrain * 255).astype(np.uint8),
                                            np.linalg.inv(np.asarray(result['roi_to_map'])), panel.shape[1::-1])
            overlay = panel.copy()
            contours, _ = cv2.findContours(recovered, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, (0, 255, 255), 1)
            cv2.drawMarker(overlay, tuple(np.rint(player).astype(int)), (0, 215, 255), cv2.MARKER_CROSS, 14, 1)
            if not cv2.imwrite(str(review), np.hstack([panel, overlay])):
                raise OSError(f'Cannot save alignment review: {review}')
        return result
