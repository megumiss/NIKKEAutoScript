import math
import re
import time
import unicodedata
from typing import Optional

import cv2
import numpy as np

from module.base.utils import extract_letters
from module.logger import logger
from module.ocr.models import OCR_MODEL


class ArenaOcr:
    FIELDS = ('Power', 'Ranking', 'CommanderLevel', 'SynchroLevel')
    CONFIDENCE_THRESHOLD = 0.51
    DIGIT_REPLACEMENTS = str.maketrans({'I': '1', '|': '1', 'O': '0', 'Q': '0', 'D': '0', 'S': '5', 'B': '8', 'G': '6'})

    def __init__(self, model_type='mobile'):
        self.model_type = model_type

    @staticmethod
    def pre_process(image: np.ndarray, field_name: str) -> np.ndarray:
        if field_name == 'SynchroLevel':
            # 卡面背景复杂，提取白色字芯，保留抗锯齿灰度而不是硬二值化。
            gray = extract_letters(image, letter=(255, 255, 255))
            scale_x = 3
        else:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            if field_name == 'CommanderLevel':
                gray = cv2.bitwise_not(gray)
            scale_x = 4

        # 游戏的窄字体直接等比缩放容易丢掉 1 或合并重复数字。
        gray = cv2.resize(gray, None, fx=scale_x, fy=2, interpolation=cv2.INTER_CUBIC)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    @classmethod
    def parse_result(cls, result: dict) -> Optional[int]:
        score = float(result.get('rec_score', 0))
        if not math.isfinite(score) or score < cls.CONFIDENCE_THRESHOLD:
            return None
        text = unicodedata.normalize('NFKC', result.get('rec_text', ''))
        text = re.sub(r'[\s,]', '', text).translate(cls.DIGIT_REPLACEMENTS)
        # 必须整行是数字，不能把 12S4 截成 12 或从混入文字的结果中截取数值。
        if not re.fullmatch(r'[0-9]+', text):
            return None
        return int(text)

    def ocr(self, image: np.ndarray, field_name: str) -> int:
        if field_name not in self.FIELDS:
            raise ValueError(f'Unknown arena OCR field: {field_name}')
        if image.size == 0:
            logger.warning(f'OCR ARENA_{field_name}: Empty crop, returning 0')
            return 0

        start = time.monotonic()
        model = OCR_MODEL.get_model_by(lang='num', model_type=self.model_type)
        result = model.recognize([self.pre_process(image, field_name)])[0]
        value = self.parse_result(result)
        if value is None:
            logger.debug(f'OCR ARENA_{field_name}: Invalid processed result {result["rec_text"]!r}, retrying raw crop')
            result = model.recognize([image])[0]
            value = self.parse_result(result)
        if value is None:
            logger.warning(
                f'OCR ARENA_{field_name}: Unreadable {result["rec_text"]!r} '
                f'(confidence={result["rec_score"]:.3f}), returning 0'
            )
            return 0

        logger.debug(f'OCR ARENA_{field_name} {time.monotonic() - start:.3f}s: {value}')
        return value
