"""YOLO observations adapted to the prototype's existing image and map coordinates."""

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .detection import detect_minimap, detect_scene


def crop_candidate(image, detection, margin=4):
    box = np.asarray(detection.box)
    left, top = np.maximum(0, np.floor(box[:2] - margin)).astype(int)
    right, bottom = np.minimum(image.shape[1::-1], np.ceil(box[2:] + margin)).astype(int)
    return image[top:bottom, left:right], np.array([left, top])


@lru_cache(maxsize=1)
def arrow_template():
    path = Path(__file__).parent / 'assets/squad_arrow_tpl.png'
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(path)
    return template


def refine_arrow(image, candidate):
    """Refine only a YOLO candidate to retain the animation calibration's template-center convention."""
    patch, offset = crop_candidate(image, candidate, margin=14)
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    masks = [cv2.inRange(hsv, (0, 0, 215), (179, 110, 255)),
             cv2.inRange(hsv, (0, 0, 245), (179, 80, 255)),
             cv2.inRange(hsv, (5, 160, 190), (30, 255, 255))]
    best = None
    for scale in (.6, .65, .7, .75, .8, .85, .9, .95, 1., 1.05, 1.1, 1.15, 1.2):
        template = cv2.resize(arrow_template(), None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        if template.shape[0] > patch.shape[0] or template.shape[1] > patch.shape[1]:
            continue
        for mask in masks:
            scores = cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, point = cv2.minMaxLoc(scores)
            center = offset + point + np.array(template.shape[::-1]) / 2
            largest_side = np.max(np.ptp(np.reshape(candidate.box, (2, 2)), axis=0))
            if np.linalg.norm(center - candidate.center) > max(12., largest_side / 3):
                continue
            if best is None or score > best[0]:
                best = score, center
    return best[1] if best is not None and best[0] >= .65 else None


def squad_arrow(image):
    candidates = [item for item in detect_scene(image) if item.label == 'scene_squad_arrow']
    centers = [refine_arrow(image, item) for item in candidates]
    centers = [center for center in centers if center is not None]
    if len(centers) != 1:
        return None
    return centers[0]


def collectible_indicator(image):
    candidates = [item for item in detect_scene(image) if item.label == 'scene_collectible_indicator']
    if not candidates:
        return None
    item = max(candidates, key=lambda candidate: candidate.confidence)
    return dict(kind='collectible_indicator', confidence=item.confidence,
                position=item.center.tolist(), detector='yolo', box=list(item.box))


def refine_ring(image, candidate):
    """Fit the observed ellipse inside a YOLO ring box, including perspective-flattened rings."""
    patch, offset = crop_candidate(image, candidate)
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    # Translucent rings inherit cyan from the road, especially in the compact map.
    white = cv2.inRange(hsv, (0, 0, 145), (179, 145, 255))
    red = cv2.inRange(hsv, (135, 45, 80), (179, 255, 255))
    white[cv2.dilate(red, np.ones((3, 3), np.uint8)) > 0] = 0
    ys, xs = np.nonzero(white)
    if len(xs) < 20:
        return None
    points = np.column_stack((xs, ys)).astype(np.float32)
    center, axes, angle = cv2.fitEllipse(points)
    axes = np.asarray(axes)
    if not np.isfinite([*center, *axes, angle]).all() or min(axes) < 6:
        return None
    radians = np.deg2rad(angle)
    rotation = np.array([[np.cos(radians), np.sin(radians)], [-np.sin(radians), np.cos(radians)]])
    normalized = (points - center) @ rotation.T / (axes / 2)
    angles = np.arctan2(normalized[:, 1], normalized[:, 0])
    coverage = len(np.unique(np.floor((angles + np.pi) * 8 / np.pi)))
    # The dashed animation can leave five of sixteen angular sectors below the white threshold.
    if coverage < 11 or np.quantile(np.abs(np.linalg.norm(normalized, axis=1) - 1), .85) > .3:
        return None
    center = np.asarray(center) + offset
    if np.any(center < candidate.box[:2]) or np.any(center > candidate.box[2:]):
        return None
    return center


def project_centers(points, matrix):
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError('Expected a finite 3x3 map projection.')
    if not points:
        return []
    points = np.asarray(points, dtype=float)
    homogeneous = np.column_stack((points, np.ones(len(points)))) @ matrix.T
    if np.any(np.abs(homogeneous[:, 2]) < 1e-8):
        raise ValueError('Marker projection reaches the perspective horizon.')
    projected = homogeneous[:, :2] / homogeneous[:, 2:]
    if not np.isfinite(projected).all():
        raise ValueError('Marker projection is not finite.')
    return projected.round(1).tolist()


def marker_candidates(image):
    height, width = image.shape[:2]
    result = []
    for item in detect_minimap(image):
        left, top, right, bottom = item.box
        if left <= 3 or top <= 3 or right >= width - 3 or bottom >= height - 3:
            continue
        if item.center[0] >= width - 75 and item.center[1] >= height - 31:
            continue
        if width < 300 and top < 29 and (left < 42 or right > width - 34):
            continue
        result.append(item)
    return result


def detect_markers(image, matrix):
    candidates = marker_candidates(image)
    players, enemies = [], []
    for item in candidates:
        if item.label == 'minimap_squad_ring':
            center = refine_ring(image, item)
            if center is not None:
                players.append(center)
        elif item.label in ('minimap_enemy_normal', 'minimap_enemy_ex'):
            enemies.append(item.center)
    return project_centers(players, matrix), project_centers(enemies, matrix)


def normal_enemy_markers(image, matrix):
    candidates = marker_candidates(image)
    normals = [item for item in candidates if item.label == 'minimap_enemy_normal']
    excluded = [item for item in candidates if item.label == 'minimap_enemy_ex']
    centers = []
    for item in normals:
        box = np.asarray(item.box)
        if any(np.all(np.minimum(box[2:], other.box[2:]) > np.maximum(box[:2], other.box[:2]))
               for other in excluded):
            continue
        patch, offset = crop_candidate(image, item, margin=0)
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        red = cv2.inRange(hsv, (135, 45, 80), (179, 255, 255))
        red |= cv2.inRange(hsv, (0, 60, 140), (8, 255, 255))
        ys, xs = np.nonzero(red)
        if len(xs) < 6:
            continue
        moments = cv2.moments(cv2.convexHull(np.column_stack((xs, ys)).astype(np.float32)))
        if moments['m00'] <= 0:
            continue
        centers.append(offset + [moments['m10'] / moments['m00'], moments['m01'] / moments['m00']])
    return project_centers(centers, matrix)


def minimap_masks(image):
    """Keep the compact-road stop criterion while obtaining the squad exclusively through YOLO."""
    if image.shape != (999, 1776, 3):
        raise ValueError('Expected a 1776x999 client frame for compact-map observation.')
    panel = cv2.resize(image, (1920, 1080))[96:307, 25:243]
    players, _ = detect_markers(panel, np.eye(3))
    if len(players) != 1:
        raise ValueError(f'Expected one compact-map squad ring, got {len(players)}.')
    player = np.asarray(players[0])
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    road = cv2.inRange(hsv, (85, 65, 115), (115, 255, 255))
    road = cv2.morphologyEx(road, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    valid = np.full(road.shape, 255, np.uint8)
    valid[:3] = valid[-3:] = 0
    valid[:, :3] = valid[:, -3:] = 0
    valid[:29, :42] = valid[:29, -34:] = valid[-31:, -76:] = 0
    bright = (hsv[:, :, 2] > 225).astype(np.uint8) * 255
    cv2.circle(bright, tuple(np.rint(player).astype(int)), 29, 0, -1)
    valid[cv2.dilate(bright, np.ones((15, 15), np.uint8)) > 0] = 0
    cv2.circle(valid, tuple(np.rint(player).astype(int)), 21, 0, -1)
    road[valid == 0] = 0
    if np.count_nonzero(road) < 200:
        raise ValueError('Too little minimap road evidence.')
    return panel, road, valid, player
