"""场景、缩略地图、展开地图专用 ONNX 推理；不发送任何游戏输入。"""

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import threading
import weakref

import cv2
import numpy as np

from module.campaign.vision.frame import CLIENT_SHAPE, inference_frame_token

SCENE_LABELS = (
    'scene_squad_arrow', 'scene_collectible_indicator', 'scene_ground_mechanism_off',
    'scene_ground_mechanism_on', 'scene_elevator_start', 'scene_elevator_end',
    'scene_barrier_curtain', 'scene_barrier_pillar', 'scene_enemy_ring',
)
MINIMAP_LABELS = ('minimap_enemy_normal', 'minimap_enemy_ex', 'minimap_squad_ring', 'minimap_enemy_boss')
ROLE_LABELS = {'scene': SCENE_LABELS, 'compact': MINIMAP_LABELS, 'expanded': MINIMAP_LABELS}
SOURCE_SIZES = {'scene': (1776, 999), 'compact': (218, 211), 'expanded': (486, 462)}
MODEL_DIRECTORY = Path(__file__).parent / 'models'
EDGE_MARGIN = 2
NMS_IOU = .45
INFERENCE_CACHE_SIZE = 4


@dataclass(frozen=True)
class Detection:
    """一个检测结果：类别、置信度和 (x1, y1, x2, y2) 框。

    坐标始终落在传入 predict 的那张图上，切片推理的偏移由调用方补回；不可变是为了能直接放进
    集合、按类别分组做抑制而不担心被就地改动。
    """

    label: str
    confidence: float
    box: tuple

    @property
    def center(self):
        """检测框的几何中心。

        这是框中心而不是符号的精确中心：小队圆环和箭头的落点需要亚像素精度，应改用
        perception 里对应的精定位，这里的中心只够做选择和展示。
        """
        return (np.asarray(self.box[:2]) + self.box[2:]) / 2


def image_size(image):
    """校验推理输入并返回高宽，空图或非 uint8 BGR 图不允许进入预处理与缓存。"""
    if image is None or image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        raise ValueError('YOLO expects a non-empty uint8 BGR image.')
    height, width = image.shape[:2]
    if min(height, width) == 0:
        raise ValueError('YOLO received an empty image.')
    return height, width


def letterbox(image, size):
    """把 BGR 图等比填入指定高宽的矩形输入张量，并返回还原检测框所需的缩放与偏移。

    等比缩放后居中填充，填充值 114 与 YOLO 训练时一致，换成 0 会在边缘造出假边。
    图像居中而非贴左上，所以还原框时必须先减 padding 再除以 scales，顺序颠倒会整体偏移。
    返回的 scales 按宽高分开给，因为 round 之后两个方向的实际比例可能差一个像素。
    """
    height, width = image_size(image)
    target_height, target_width = size
    scale = min(target_width / width, target_height / height)
    resized = (round(width * scale), round(height * scale))
    left, top = (target_width - resized[0]) // 2, (target_height - resized[1]) // 2
    canvas = np.full((target_height, target_width, 3), 114, np.uint8)
    canvas[top:top + resized[1], left:left + resized[0]] = cv2.resize(image, resized)
    tensor = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255
    return tensor, (resized[0] / width, resized[1] / height), (left, top)


def suppress(detections, iou=NMS_IOU):
    """按类别分别做非极大值抑制，返回按置信度降序的结果。

    必须分类别做：同一处会同时出现屏障立柱与光幕、机关的开与关，跨类别抑制会把其中一个吃掉。
    置信度阈值传 0，因为类别阈值已经在 decode 里按模型元数据应用过，这里只去重叠框。
    """
    selected = []
    for label in sorted({item.label for item in detections}):
        group = [item for item in detections if item.label == label]
        boxes = [[b.box[0], b.box[1], b.box[2] - b.box[0], b.box[3] - b.box[1]] for b in group]
        indices = cv2.dnn.NMSBoxes(boxes, [b.confidence for b in group], 0., iou)
        selected.extend(group[int(index)] for index in np.asarray(indices).reshape(-1))
    return sorted(selected, key=lambda item: item.confidence, reverse=True)


def decode(output, scales, padding, thresholds, shape, labels, guards=(True, True, True, True)):
    """把原始输出解成图像坐标检测框；thresholds 的键同时决定参与解码的类别。

    guards 按左、上、右、下指明需要拒绝截断框的图像边界；小地图必须拒绝截边标记。
    场景结果可以裁到画面边界，是否可用于点击仍由各目标的导航安全检查决定。
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
        # 整图矩形补边中的预测可能在裁回原图后消失，不能返回零面积目标。
        if min(box[2:] - box[:2]) < 3:
            continue
        result.append(Detection(labels[label], float(score), tuple(float(v) for v in box)))
    return suppress(result)


class Detector:
    """校验并持有一个专用模型，同帧消费者共享原始输出，各自独立筛选类别与阈值。"""

    def __init__(self, role, directory=MODEL_DIRECTORY, intra_op_threads=4):
        """只加载同一发布包声明的角色，不接受旧十三类模型或隐式回退。

        ONNX 算子线程数不改变输入、精度与解码规则。会话 Run 串行保护，同键并发请求
        共用 Future；缓存只保存模型输出，不把某个调用方的阈值筛选结果交给其他调用方。
        """
        if role not in ROLE_LABELS:
            raise ValueError(f'Unknown campaign detector role: {role}')
        if type(intra_op_threads) is not int or intra_op_threads < 1:
            raise ValueError('intra_op_threads must be a positive integer')
        directory = Path(directory)
        bundle = json.loads((directory / 'bundle.json').read_text(encoding='utf-8'))
        if (bundle.get('schema_version') != 3 or set(bundle.get('models', {})) != set(ROLE_LABELS)
                or not isinstance(bundle.get('version'), str) or not bundle['version']):
            raise ValueError('Campaign bundle must contain scene, compact and expanded models.')
        entry = bundle['models'][role]
        if entry != {'model': f'{role}.onnx', 'metadata': f'{role}.json'}:
            raise ValueError('Campaign bundle uses unexpected model paths.')
        metadata = json.loads((directory / entry['metadata']).read_text(encoding='utf-8'))
        if (metadata.get('schema_version') != 3 or metadata.get('role') != role
                or metadata.get('version') != bundle.get('version')
                or metadata.get('format') != 'yolo11-detect-bcn-fp32'
                or metadata.get('labels') != list(ROLE_LABELS[role])
                or metadata.get('source_size') != list(SOURCE_SIZES[role])
                or metadata.get('class_selection') != 'argmax_all_role_classes'
                or metadata.get('color') != 'RGB' or metadata.get('normalization') != 'float32 / 255'
                or metadata.get('layout') != 'NCHW' or metadata.get('box_format') != 'cxcywh_input_pixels'
                or metadata.get('nms_iou') != NMS_IOU or metadata.get('edge_margin') != EDGE_MARGIN):
            raise ValueError(f'Invalid campaign {role} model contract.')
        shape = metadata.get('input_shape', [])
        if (len(shape) != 4 or shape[:2] != [1, 3]
                or any(type(value) is not int or value < 32 or value % 32 for value in shape[2:])):
            raise ValueError(f'Invalid campaign {role} input shape: {shape}')
        self.role, self.metadata = role, metadata
        self.labels, self.input_size = tuple(metadata['labels']), tuple(shape[2:])
        self.thresholds = metadata.get('thresholds', {})
        if (not isinstance(self.thresholds, dict) or set(self.thresholds) != set(self.labels)
                or any(type(value) not in (int, float) or not 0 < value <= 1
                       for value in self.thresholds.values())):
            raise ValueError(f'Invalid campaign {role} thresholds.')
        model = directory / entry['model']
        if hashlib.sha256(model.read_bytes()).hexdigest() != metadata.get('sha256'):
            raise ValueError(f'Campaign {role} ONNX hash mismatch.')
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError('Install requirements-yolo.txt to run campaign detection.') from exc
        options = ort.SessionOptions()
        options.intra_op_num_threads = intra_op_threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        anchors = sum((shape[2] // stride) * (shape[3] // stride) for stride in (8, 16, 32))
        output_shape = [1, 4 + len(self.labels), anchors]
        if (len(inputs) != 1 or inputs[0].shape != shape or inputs[0].type != 'tensor(float)'
                or len(outputs) != 1 or outputs[0].shape != output_shape or outputs[0].type != 'tensor(float)'
                or metadata.get('output_shape') != output_shape
                or inputs[0].name != metadata.get('input_name')
                or outputs[0].name != metadata.get('output_name')):
            raise ValueError(f'Campaign {role} ONNX signature mismatch.')
        self.input_name, self.output_name = inputs[0].name, outputs[0].name
        self.lock, self.run_lock = threading.Lock(), threading.Lock()
        self.inference_cache, self.pending = OrderedDict(), {}

    def selected(self, labels):
        """校验当前角色可识别的类别，拒绝把场景目标交给小地图模型或反向使用。"""
        missing = set(labels) - set(self.labels)
        if missing:
            raise ValueError(f'Campaign {self.role} model lacks classes: {sorted(missing)}')
        return {label: self.thresholds[label] for label in labels}

    def infer(self, image, *, enhance_curtain=False):
        """推理完整输入并缓存原始张量，同帧同一路输入的类别筛选与低阈值复核共享输出。

        enhance_curtain 仅用于场景光幕：原尺寸灰度图经 CLAHE（clipLimit=2、8×8 网格）后
        转回三通道，不改变业务原图或坐标。缓存键区分原图与增强图，并包含原始像素摘要，
        原地改图不会误用旧输出。观测作用域内允许等像素的不同数组复用，作用域外只在同一
        数组对象内复用。异常必须通知所有同键等待者并允许重试。
        """
        height, width = image_size(image)
        if (width, height) != SOURCE_SIZES[self.role]:
            raise ValueError(f'Campaign {self.role} input expects {SOURCE_SIZES[self.role]}, got {(width, height)}.')
        if enhance_curtain and self.role != 'scene':
            raise ValueError('Curtain enhancement requires the campaign scene model.')
        token = inference_frame_token()
        key = (token if token is not None else id(image),
               hashlib.sha256(np.ascontiguousarray(image)).digest(), enhance_curtain)
        with self.lock:
            cached = self.inference_cache.get(key)
            if cached is not None and (token is not None or cached[0]() is image):
                self.inference_cache.move_to_end(key)
                return cached[1:]
            pending = self.pending.get(key)
            owner = pending is None
            if owner:
                pending = self.pending[key] = Future()
        if not owner:
            return pending.result()
        try:
            prepared = image
            if enhance_curtain:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                enhanced = cv2.createCLAHE(clipLimit=2., tileGridSize=(8, 8)).apply(gray)
                prepared = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
            tensor, scales, padding = letterbox(prepared, self.input_size)
            with self.run_lock:
                output = self.session.run([self.output_name], {self.input_name: tensor})[0]
            result = output, scales, padding
            with self.lock:
                self.inference_cache[key] = (weakref.ref(image), *result)
                self.inference_cache.move_to_end(key)
                while len(self.inference_cache) > INFERENCE_CACHE_SIZE:
                    self.inference_cache.popitem(last=False)
                self.pending.pop(key)
            pending.set_result(result)
            return result
        except BaseException as exc:
            with self.lock:
                self.pending.pop(key, None)
            pending.set_exception(exc)
            raise

    def predict(self, image, labels=None):
        """在专用模型全部类别中判类，再按请求筛选，所有框仍为原图像素坐标。

        光幕只采用灰度增强输入，其余场景类别及小地图保持原图；只推理请求涉及的输入路。
        每路独立沿用模型阈值和分类 NMS，不把增强后的立柱、箭头或机关并入原图结果。
        """
        thresholds = self.thresholds.copy() if labels is None else self.selected(labels)
        curtain_threshold = thresholds.pop('scene_barrier_curtain', None)
        guards = (self.role != 'scene',) * 4
        detections = []
        if thresholds:
            output, scales, padding = self.infer(image)
            detections.extend(decode(output, scales, padding, thresholds, image.shape, self.labels, guards))
        if curtain_threshold is not None:
            output, scales, padding = self.infer(image, enhance_curtain=True)
            detections.extend(decode(output, scales, padding, {'scene_barrier_curtain': curtain_threshold},
                                     image.shape, self.labels, guards))
        return sorted(detections, key=lambda item: item.confidence, reverse=True)


def package_signature():
    """读取整包及三份元数据的指纹，不允许新增角色混入进程启动后的另一模型版本。"""
    digest = hashlib.sha256((MODEL_DIRECTORY / 'bundle.json').read_bytes())
    for role in ROLE_LABELS:
        digest.update((MODEL_DIRECTORY / f'{role}.json').read_bytes())
    return digest.digest()


@lru_cache(maxsize=1)
def loaded_package_signature():
    """锁定本进程首次加载的模型包，磁盘更新后需重启任务而不是混用新旧角色。"""
    return package_signature()


@lru_cache(maxsize=3)
def detector(role):
    """每个角色共享一个进程内会话，只有实际用到的模型才加载。"""
    signature = loaded_package_signature()
    model = Detector(role, directory=MODEL_DIRECTORY)
    if package_signature() != signature:
        raise RuntimeError('Campaign model bundle changed; restart the task before loading another role.')
    return model


def detect_scene(image, labels=SCENE_LABELS):
    """对整幅客户区识别场景，光幕使用灰度增强图，其余类别使用原图；同帧每路复用输出。"""
    if image is None or image.shape != CLIENT_SHAPE:
        raise ValueError('Campaign scene detection expects a 1776x999 BGR client frame.')
    return detector('scene').predict(image, labels)


def detect_minimap(image, mode, labels=MINIMAP_LABELS):
    """按明确的缩略/展开状态使用对应专用模型；未知状态不得猜测或同时运行两种模型。"""
    if mode not in ('compact', 'expanded'):
        raise ValueError(f'Explicit compact or expanded minimap mode required: {mode}')
    return detector(mode).predict(image, labels)
