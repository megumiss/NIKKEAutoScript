"""一次截图的共享输入、互斥小地图状态和推理缓存命名空间。"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import cached_property

import cv2
import numpy as np

from module.campaign.vision.minimap import map_visible, minimap_control_visible

CLIENT_SHAPE = (999, 1776, 3)
COMPACT_ROI = (25, 96, 243, 307)
EXPANDED_ROI = (644, 280, 1130, 742)
_CURRENT_FRAME = ContextVar('campaign_vision_frame', default=None)


def minimap_state(image):
    """识别完整客户区的小地图形态，返回 compact、expanded，关闭或未知时返回 None。

    展开态必须同时具有中央地图面板及其左上控件，并且左上紧凑控件已经消失。
    部分章节展开后仍有紧凑面板的暗色残影，因此不能先凭残影否决展开态。
    单独的大面积青蓝色可能来自雪地等场景，不能作为展开或输入坐标可用的依据。
    """
    compact_control = minimap_control_visible(image[76:120, 20:64])
    left, top, right, bottom = EXPANDED_ROI
    x, y = left + 14, top - 11
    if (not compact_control and map_visible(image[top:bottom, left:right])
            and minimap_control_visible(image[y - 22:y + 22, x - 22:x + 22])):
        return 'expanded'
    if compact_control and map_visible(image[118:273, 27:207]):
        return 'compact'
    return None


class FrameInputs:
    """在一次观测期间复用裁图，缩略与展开只会选中一个，不跨截图沿用状态。

    创建时保存原图的只读副本，不改变调用方数组；外部继续截图或改图不会混入本次观测。
    派生面板也是独立只读数组，避免某个消费者污染其他消费者与检测缓存。
    """

    def __init__(self, image):
        """校验客户区输入并分配独立缓存令牌，避免不同截图的结果相互混用。"""
        if image is None or image.shape != CLIENT_SHAPE or image.dtype != np.uint8:
            raise ValueError('Campaign observation expects a 1776x999 uint8 BGR client frame.')
        self.source = image
        self.image = image.copy()
        self.image.flags.writeable = False
        self.token = object()

    @cached_property
    def compact(self):
        """按既有标定先归一化至 1920×1080，再裁出 218×211 缩略面板。"""
        left, top, right, bottom = COMPACT_ROI
        panel = cv2.resize(self.image, (1920, 1080))[top:bottom, left:right].copy()
        panel.flags.writeable = False
        return panel

    @cached_property
    def expanded(self):
        """保留展开面板的原始 486×462 像素，不进行第二次缩放或重新裁边。"""
        left, top, right, bottom = EXPANDED_ROI
        panel = self.image[top:bottom, left:right].copy()
        panel.flags.writeable = False
        return panel

    @cached_property
    def mode(self):
        """使用完整客户区的共享判据，过渡帧不能用上一次模式猜测当前面板。"""
        return minimap_state(self.image)

    @property
    def panel(self):
        """只返回当前选中的面板；关闭或未知状态不触发任一小地图模型。"""
        return getattr(self, self.mode) if self.mode is not None else None


def frame_inputs(image):
    """在当前观测内返回同一输入对象，作用域外则创建不会跨调用残留的临时对象。"""
    current = _CURRENT_FRAME.get()
    if current is not None and (current.image is image or current.source is image):
        return current
    return FrameInputs(image)


def inference_frame_token():
    """为同帧的不同裁图数组提供共同命名空间，便于共享相同像素的原始模型输出。"""
    current = _CURRENT_FRAME.get()
    return current.token if current is not None else None


@contextmanager
def observation_frame(image):
    """建立可嵌套的观测作用域，异常也必须恢复原上下文，不把半帧状态留给下一次。"""
    inputs = frame_inputs(image)
    token = _CURRENT_FRAME.set(inputs)
    try:
        yield inputs
    finally:
        _CURRENT_FRAME.reset(token)
