"""自动主线剧情与收集品任务的公共准备流程。"""

from functools import cached_property

import cv2

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.campaign.chapter import CLIENT_SIZE, chapter_number
from module.config.config import TaskEnd
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.constant import ModelsPath
from module.ocr.models import get_ocr_cpu_threads, get_ocr_device
from module.ocr.progress import OcrInitProgress


class Campaign(ModuleBase):
    RESOLUTION = CLIENT_SIZE
    current_chapter = None

    @cached_property
    def chapter_model(self):
        with OcrInitProgress('Preparing campaign chapter OCR'):
            from paddleocr import TextRecognition

            from module.ocr.download import maybe_download
            from module.ocr.nikke_ocr import models

            model_name = 'PP-OCRv5_mobile_rec_infer'
            model_dir = maybe_download(ModelsPath / model_name, models[model_name])
            return TextRecognition(
                model_name='PP-OCRv5_mobile_rec', model_dir=str(model_dir),
                device=get_ocr_device(), cpu_threads=get_ocr_cpu_threads(),
            )

    def detect_current_chapter(self):
        self.current_chapter = None
        model = self.chapter_model
        timeout = Timer(30).start()
        previous = None
        stable = 0
        while 1:
            if self.config.stop_event is not None and self.config.stop_event.is_set():
                raise TaskEnd
            self.device.screenshot()
            # 设备截图统一为 RGB，Paddle 的 ndarray 输入使用 BGR。
            image = cv2.cvtColor(self.device.image, cv2.COLOR_RGB2BGR)
            number = chapter_number(image, model)
            if number is not None and number == previous:
                stable += 1
            else:
                stable = 1 if number is not None else 0
            if number != previous:
                logger.info(f'主线章节识别候选：{number}')
            previous = number
            if stable >= 3:
                self.current_chapter = number
                logger.info(f'当前主线章节：第 {number} 章')
                return number
            if timeout.reached():
                logger.error('无法确认当前主线章节，请进入主线地图页面，确保右下角章节号可见后重试。')
                raise RequestHumanTakeover
            self.device.sleep(0.5)

    def run(self):
        logger.hr(self.config.task.command, level=1)
        self.device.check_resolution(*self.RESOLUTION)
        logger.info('主线任务分辨率：1776×999')
        chapter = self.detect_current_chapter()
        logger.info('主线准备完成；当前阶段仅设置分辨率并识别章节。')
        return chapter
