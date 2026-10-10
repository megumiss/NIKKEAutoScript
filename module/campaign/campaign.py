"""自动主线剧情与收集品任务的公共准备流程。"""

import time
from functools import cached_property

import cv2

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.base.utils import publish_preview_frame
from module.campaign.chapter import CLIENT_SIZE, chapter_identity
from module.campaign.observation import CampaignObserver
from module.config.config import TaskEnd
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Ocr


class Campaign(ModuleBase):
    RESOLUTION = CLIENT_SIZE
    current_chapter = None
    current_difficulty = None

    @cached_property
    def chapter_ocr(self):
        return Ocr([], name='CampaignIdentity', lang='ch', model_type=self.config.Optimization_OcrModelType)

    @cached_property
    def observer(self):
        return CampaignObserver()

    def detect_current_chapter(self):
        self.current_chapter = self.current_difficulty = None
        reader = self.chapter_ocr
        timeout = Timer(30).start()
        previous, stable = None, 0
        while 1:
            if self.config.stop_event is not None and self.config.stop_event.is_set():
                raise TaskEnd
            self.device.screenshot()
            captured_at = time.time()
            # 设备截图统一为 RGB，OCR 与地图检测的 ndarray 输入使用 BGR。
            image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
            identity = chapter_identity(image, reader)
            stable = stable + 1 if identity is not None and identity == previous else int(identity is not None)
            previous = identity
            if stable >= 3:
                self.current_chapter, self.current_difficulty = identity
                logger.info(f'当前主线章节：{identity[1].upper()} 第 {identity[0]} 章')
                snapshot = self.observer.observe(image, identity, captured_at)
                publish_preview_frame(self.device.image, interval=0, source={'campaign': snapshot})
                return self.current_chapter
            if timeout.reached():
                logger.error('无法确认主线章节及难度，请确保主线地图右下角章节号和难度标识可见。')
                raise RequestHumanTakeover
            self.device.sleep(0.5)

    def run(self):
        logger.hr(self.config.task.command, level=1)
        self.device.check_resolution(*self.RESOLUTION)
        logger.info('主线任务分辨率：1776×999')
        chapter = self.detect_current_chapter()
        # TODO: 根据任务类型执行推图，并在移动决策与战斗前后发布章节状态。
        return chapter
