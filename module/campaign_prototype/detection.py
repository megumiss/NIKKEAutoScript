"""YOLO ONNX inference in image coordinates; this module never sends game input."""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import threading

import cv2
import numpy as np

# 旧五类沿用各自验证过的取图方式：场景箭头与收集提示走遮罩切片，小地图标记走面板裁剪。
LEGACY_SCENE_LABELS = ('scene_squad_arrow', 'scene_collectible_indicator')
MINIMAP_LABELS = ('minimap_enemy_normal', 'minimap_enemy_ex', 'minimap_squad_ring')
# 机关、电梯、屏障在整幅客户区的网格上检测，网格与边界规则来自模型包的 scene 契约。
GRID_LABELS = ('scene_ground_mechanism_off', 'scene_ground_mechanism_on', 'scene_elevator_start',
               'scene_elevator_end', 'scene_barrier_curtain', 'scene_barrier_pillar')
# 应用已知的类别集合；实际类别顺序始终由模型元数据决定，这里不假定模型包含哪些类。
LABELS = LEGACY_SCENE_LABELS + MINIMAP_LABELS + GRID_LABELS
MODEL_DIRECTORY = Path(__file__).parent / 'models'
EDGE_MARGIN = 2
NMS_IOU = .45


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


def suppress(detections, iou=NMS_IOU):
    selected = []
    for label in sorted({item.label for item in detections}):
        group = [item for item in detections if item.label == label]
        boxes = [[b.box[0], b.box[1], b.box[2] - b.box[0], b.box[3] - b.box[1]] for b in group]
        indices = cv2.dnn.NMSBoxes(boxes, [b.confidence for b in group], 0., iou)
        selected.extend(group[int(index)] for index in np.asarray(indices).reshape(-1))
    return sorted(selected, key=lambda item: item.confidence, reverse=True)


def decode(output, scales, padding, thresholds, shape, labels, guards=(True, True, True, True)):
    """把原始输出解成图像坐标检测框；thresholds 的键同时决定参与解码的类别。

    guards 按左、上、右、下指明哪些边是切片内部边界：内部边界上的框是被切断的符号，中心会偏，
    必须丢弃；画面真实边界上的目标是完整的，只把框裁回画面。
    """
    if output.ndim != 3 or output.shape[0] != 1 or output.shape[1] != 4 + len(labels):
        raise ValueError(f'Unsupported YOLO output {output.shape}; expected [1, {4 + len(labels)}, anchors].')
    rows = output[0].T
    rows = rows[np.isfinite(rows).all(axis=1)]
    if not len(rows):
        return []
    scores = rows[:, 4:]
    if np.any(scores < 0) or np.any(scores > 1):
        raise ValueError('YOLO class scores are not probabilities.')
    classes = scores.argmax(axis=1)
    confidence = scores[np.arange(len(rows)), classes]
    # 未请求的类别给出无穷阈值；类别仍由全类 argmax 决定，不在子集上重新取最大值。
    limits = np.asarray([thresholds.get(label, np.inf) for label in labels], float)
    keep = confidence >= limits[classes]
    rows, classes, confidence = rows[keep], classes[keep], confidence[keep]
    boxes = np.column_stack((rows[:, :2] - rows[:, 2:4] / 2, rows[:, :2] + rows[:, 2:4] / 2))
    boxes = (boxes - np.tile(padding, 2)) / np.tile(scales, 2)
    height, width = shape[:2]
    inner = (EDGE_MARGIN, EDGE_MARGIN, width - EDGE_MARGIN, height - EDGE_MARGIN)
    result = []
    for box, label, score in zip(boxes, classes, confidence):
        truncated = (box[0] < inner[0], box[1] < inner[1], box[2] > inner[2], box[3] > inner[3])
        if any(cut and guard for cut, guard in zip(truncated, guards)):
            continue
        if min(box[2:] - box[:2]) < 3:
            continue
        box = np.clip(box, 0, (width, height, width, height))
        result.append(Detection(labels[label], float(score), tuple(float(v) for v in box)))
    return suppress(result)


class Detector:
    def __init__(self, directory=MODEL_DIRECTORY):
        directory = Path(directory)
        metadata_path = directory / 'campaign.json'
        self.metadata = json.loads(metadata_path.read_text('utf-8'))
        if self.metadata.get('schema_version') not in (1, 2):
            raise ValueError('Unsupported campaign YOLO metadata schema.')
        if self.metadata.get('format') != 'yolo11-detect-bcn-fp32':
            raise ValueError('Unsupported campaign YOLO export format.')
        labels = self.metadata.get('labels')
        if (not isinstance(labels, list) or not labels or len(set(labels)) != len(labels)
                or any(label not in LABELS for label in labels)):
            raise ValueError('Campaign YOLO metadata declares unknown or duplicated classes.')
        self.labels = tuple(labels)
        self.size = self.metadata['input_size']
        if not isinstance(self.size, int) or self.size < 320 or self.size % 32:
            raise ValueError('Invalid YOLO input size.')
        self.thresholds = self.metadata['thresholds']
        if any(label not in self.thresholds or not 0 < self.thresholds[label] < 1 for label in self.labels):
            raise ValueError('Invalid YOLO class thresholds.')
        self.scene = self.scene_contract(self.metadata.get('scene'))
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

    def scene_contract(self, scene):
        """校验模型包声明的全画面网格；边界余量和 NMS 阈值必须与本模块实现一致。"""
        if scene is None:
            return None
        size, tile = scene.get('size'), scene.get('tile_size')
        offsets = (scene.get('x_offsets'), scene.get('y_offsets'))
        if (not isinstance(size, list) or len(size) != 2 or type(tile) is not int or tile != self.size
                or any(not isinstance(values, list) or not values for values in offsets)):
            raise ValueError('Invalid campaign YOLO scene grid.')
        for values, bound in zip(offsets, size):
            if any(type(value) is not int or not 0 <= value <= bound - tile for value in values):
                raise ValueError('Campaign YOLO scene grid leaves the client frame.')
        if scene.get('internal_edge_margin') != EDGE_MARGIN or scene.get('classwise_nms_iou') != NMS_IOU:
            raise ValueError('Campaign YOLO scene grid expects different edge or NMS handling.')
        return dict(size=tuple(size), tile=tile, x_offsets=tuple(offsets[0]), y_offsets=tuple(offsets[1]))

    def selected(self, labels):
        """按请求类别裁剪阈值表；模型没有该类时明确失败，不静默返回空结果。"""
        missing = [label for label in labels if label not in self.labels]
        if missing:
            raise ValueError(f'Campaign YOLO model lacks classes: {", ".join(missing)}.')
        return {label: self.thresholds[label] for label in labels}

    def predict(self, image, labels=None, guards=(True, True, True, True)):
        thresholds = self.thresholds if labels is None else self.selected(labels)
        tensor, scales, padding = letterbox(image, self.size)
        with self.lock:
            result = self.session.run([self.output_name], {self.input_name: tensor})[0]
        return decode(result, scales, padding, thresholds, image.shape, self.labels, guards)


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


def detect_scene(image, labels=LEGACY_SCENE_LABELS):
    detections = []
    for tile, offset in scene_tiles(image):
        for item in detector().predict(tile, labels):
            box = tuple(np.asarray(item.box) + np.tile(offset, 2))
            detections.append(Detection(item.label, item.confidence, box))
    return suppress(detections)


def detect_minimap(image, labels=MINIMAP_LABELS):
    return detector().predict(image, labels)


def grid_tiles(image):
    """按模型包声明的网格切分整幅客户区，并给出每片哪些边属于切片内部边界。"""
    scene = detector().scene
    if scene is None:
        raise ValueError('Campaign YOLO model package declares no scene grid.')
    if image.shape[1::-1] != scene['size']:
        raise ValueError(f'Scene grid expects a {scene["size"][0]}x{scene["size"][1]} BGR client frame.')
    tile, (width, height) = scene['tile'], scene['size']
    for top in scene['y_offsets']:
        for left in scene['x_offsets']:
            guards = (left > 0, top > 0, left + tile < width, top + tile < height)
            yield image[top:top + tile, left:left + tile], (left, top), guards


def detect_grid(image, labels=GRID_LABELS):
    """在整幅客户区上检测机关、电梯与屏障；坐标为客户区像素。

    八片 640×640 覆盖整幅画面，逐片抑制后再全局抑制，与模型包的 scene 契约一致。
    """
    detections = []
    for tile, offset, guards in grid_tiles(image):
        for item in detector().predict(tile, labels, guards):
            box = tuple(np.asarray(item.box) + np.tile(offset, 2))
            detections.append(Detection(item.label, item.confidence, box))
    return suppress(detections)
