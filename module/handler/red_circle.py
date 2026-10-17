import cv2
import numpy as np

_ANGLES = np.arange(72) * (2 * np.pi / 72)
_DIRECTIONS = np.stack((np.cos(_ANGLES), np.sin(_ANGLES)), axis=1)[:, None, :]
_RADIUS_OFFSETS = np.arange(-1.5, 2, 0.5)[None, :, None]


def _ring_support(mask, x, y, radius):
    positions = np.rint((x, y) + _DIRECTIONS * (radius + _RADIUS_OFFSETS)).astype(int)
    px, py = positions[:, :, 0], positions[:, :, 1]
    height, width = mask.shape
    valid = (px >= 0) & (px < width) & (py >= 0) & (py < height)
    values = mask[np.clip(py, 0, height - 1), np.clip(px, 0, width - 1)]
    return np.any((values > 0) & valid, axis=1)


def find_red_circles(image):
    """Return deduplicated RGB screenshot circle centers, ordered by red/yellow ring coverage."""
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        return []
    if image.shape[0] < 77 or image.shape[1] < 2:
        return []

    # Match the combat region used by the handler; half resolution keeps contour work inexpensive.
    small = cv2.resize(image[75:851], None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    red = cv2.inRange(hsv, (0, 130, 80), (15, 255, 255)) | cv2.inRange(hsv, (170, 130, 80), (179, 255, 255))
    yellow = cv2.inRange(hsv, (16, 100, 170), (40, 255, 255))
    candidates = []

    # Suppress red effect stippling. Keep raw yellow arcs too: denoising can erase a thin/occluded ring.
    masks = ((cv2.medianBlur(red, 3), 1.0), (yellow, 1.11), (cv2.medianBlur(yellow, 3), 1.11))
    for mask, radius_scale in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            if len(contour) < 12:
                continue
            _, _, width, height = cv2.boundingRect(contour)
            if not (15 <= width <= 115 and 15 <= height <= 115):
                continue

            # Fit a circle rather than an ellipse so a visible arc still gives a stable center.
            points = contour[:, 0, :].astype(np.float32)
            origin = points.mean(axis=0)
            points -= origin
            matrix = np.column_stack((2 * points, np.ones(len(points))))
            fit, _, _, _ = np.linalg.lstsq(matrix, (points * points).sum(axis=1), rcond=None)
            x, y = fit[:2] + origin
            radius = np.sqrt(max(0, fit[2] + fit[0] ** 2 + fit[1] ** 2))
            if not 15 < radius < 52:
                continue
            residual = np.mean(np.abs(np.linalg.norm(contour[:, 0, :] - (x, y), axis=1) - radius))
            if residual > max(1.2, radius * 0.055):
                continue
            center_x, center_y = int(round(x * 2)), int(round(y * 2 + 75))
            if not (0 <= center_x < image.shape[1] and 75 <= center_y <= min(850, image.shape[0] - 1)):
                continue

            # A yellow reticle or red damage text alone is not a target: require concentric colors.
            radius *= radius_scale
            paired = _ring_support(red, x, y, radius) & _ring_support(yellow, x, y, radius * 0.9)
            coverage = np.mean(paired)
            if coverage < 0.35:
                continue
            # Reject broad yellow/orange muzzle flashes rather than a thin yellow ring.
            if np.mean(paired & ~_ring_support(yellow, x, y, radius * 0.7)) < 0.35:
                continue
            candidates.append((coverage, x * 2, y * 2 + 75, radius * 2))

    candidates.sort(key=lambda circle: (-circle[0], circle[2], circle[1]))
    circles = []
    for _, x, y, radius in candidates:
        if any(np.hypot(x - cx, y - cy) < min(radius, cr) for cx, cy, cr in circles):
            continue
        circles.append((x, y, radius))
    return [(int(round(x)), int(round(y))) for x, y, _ in circles]
