"""YOLO ONNX inference in image coordinates; this module never sends game input."""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import threading

import cv2
import numpy as np

LABELS = ('scene_squad_arrow', 'scene_collectible_indicator', 'minimap_enemy_normal',
          'minimap_enemy_ex', 'minimap_squad_ring')
MODEL_DIRECTORY = Path(__file__).parent / 'models'


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: tuple

    @property
    def center(self):
        return (np.asarray(self.box[:2]) + self.box[2:]) / 2


def letterbox(image, size):
    if image is None or image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        raise ValueError('YOLO expects a non-empty uint8 BGR image.')
    height, width = image.shape[:2]
    if min(height, width) == 0:
        raise ValueError('YOLO received an empty image.')
    scale = min(size / width, size / height)
    resized = (round(width * scale), round(height * scale))
    left, top = (size - resized[0]) // 2, (size - resized[1]) // 2
    canvas = np.full((size, size, 3), 114, np.uint8)
    canvas[top:top + resized[1], left:left + resized[0]] = cv2.resize(image, resized)
    tensor = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255
    return tensor, (resized[0] / width, resized[1] / height), (left, top)


def suppress(detections, iou=.45):
    selected = []
    for label in LABELS:
        group = [item for item in detections if item.label == label]
        if not group:
            continue
        boxes = [[b.box[0], b.box[1], b.box[2] - b.box[0], b.box[3] - b.box[1]] for b in group]
        indices = cv2.dnn.NMSBoxes(boxes, [b.confidence for b in group], 0., iou)
        selected.extend(group[int(index)] for index in np.asarray(indices).reshape(-1))
    return sorted(selected, key=lambda item: item.confidence, reverse=True)


def decode(output, scales, padding, thresholds, shape):
    if output.ndim != 3 or output.shape[0] != 1 or output.shape[1] != 4 + len(LABELS):
        raise ValueError(f'Unsupported YOLO output {output.shape}; expected [1, 9, anchors].')
    rows = output[0].T
    rows = rows[np.isfinite(rows).all(axis=1)]
    if not len(rows):
        return []
    scores = rows[:, 4:]
    if np.any(scores < 0) or np.any(scores > 1):
        raise ValueError('YOLO class scores are not probabilities.')
    classes = scores.argmax(axis=1)
    confidence = scores[np.arange(len(rows)), classes]
    keep = confidence >= np.asarray([thresholds[LABELS[c]] for c in classes])
    rows, classes, confidence = rows[keep], classes[keep], confidence[keep]
    boxes = np.column_stack((rows[:, :2] - rows[:, 2:4] / 2, rows[:, :2] + rows[:, 2:4] / 2))
    boxes = (boxes - np.tile(padding, 2)) / np.tile(scales, 2)
    height, width = shape[:2]
    result = []
    for box, label, score in zip(boxes, classes, confidence):
        # Clipping would turn a partial symbol into a plausible but biased navigation center.
        if box[0] < 2 or box[1] < 2 or box[2] > width - 2 or box[3] > height - 2:
            continue
        if min(box[2:] - box[:2]) < 3:
            continue
        result.append(Detection(LABELS[label], float(score), tuple(float(v) for v in box)))
    return suppress(result)


class Detector:
    def __init__(self, directory=MODEL_DIRECTORY):
        directory = Path(directory)
        metadata_path = directory / 'campaign.json'
        self.metadata = json.loads(metadata_path.read_text('utf-8'))
        if self.metadata.get('schema_version') != 1 or self.metadata.get('labels') != list(LABELS):
            raise ValueError('Unsupported campaign YOLO metadata or class order.')
        if self.metadata.get('format') != 'yolo11-detect-bcn-fp32':
            raise ValueError('Unsupported campaign YOLO export format.')
        self.size = self.metadata['input_size']
        if not isinstance(self.size, int) or self.size < 320 or self.size % 32:
            raise ValueError('Invalid YOLO input size.')
        self.thresholds = self.metadata['thresholds']
        if any(label not in self.thresholds or not 0 < self.thresholds[label] < 1 for label in LABELS):
            raise ValueError('Invalid YOLO class thresholds.')
        model = directory / 'campaign.onnx'
        if hashlib.sha256(model.read_bytes()).hexdigest() != self.metadata['sha256']:
            raise ValueError('Campaign YOLO model hash mismatch.')
        try:
            import onnxruntime as ort
        except ImportError as error:
            raise RuntimeError('Install requirements-yolo.txt to run campaign YOLO detection.') from error
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or inputs[0].shape != [1, 3, self.size, self.size] or inputs[0].type != 'tensor(float)':
            raise ValueError('Campaign YOLO input contract mismatch.')
        if len(outputs) != 1:
            raise ValueError('Campaign YOLO must have one raw detection output.')
        self.input_name, self.output_name = inputs[0].name, outputs[0].name
        self.lock = threading.Lock()

    def predict(self, image):
        tensor, scales, padding = letterbox(image, self.size)
        with self.lock:
            result = self.session.run([self.output_name], {self.input_name: tensor})[0]
        return decode(result, scales, padding, self.thresholds, image.shape)


@lru_cache(maxsize=1)
def detector():
    return Detector()


def scene_mask(image):
    """Exclude map overlays before tiling so their symbols cannot become scene targets."""
    if image.shape != (999, 1776, 3):
        raise ValueError('Campaign scene detection expects a 1776x999 BGR client frame.')
    result = image.copy()
    result[:120] = result[900:] = 0
    result[:295, :230] = 0
    result[:, 1650:] = 0
    hsv = cv2.cvtColor(image[280:742, 644:1130], cv2.COLOR_BGR2HSV)
    if np.mean((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 115) & (hsv[:, :, 1] > 40)) >= .85:
        result[250:790, 625:1150] = 0
    return result


def scene_tiles(image):
    masked = scene_mask(image)
    for top in (120, 260):
        for left in (0, 505, 1010):
            yield masked[top:top + 640, left:left + 640], (left, top)


def detect_scene(image):
    detections = []
    for tile, offset in scene_tiles(image):
        for item in detector().predict(tile):
            if not item.label.startswith('scene_'):
                continue
            box = tuple(np.asarray(item.box) + np.tile(offset, 2))
            detections.append(Detection(item.label, item.confidence, box))
    return suppress(detections)


def detect_minimap(image):
    return [item for item in detector().predict(image) if item.label.startswith('minimap_')]
