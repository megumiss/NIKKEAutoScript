import math
import re
import unicodedata
from typing import Optional, Tuple

import cv2
import numpy as np

from module.logger import logger
from module.ocr.models import OCR_MODEL
from module.ocr.warehouse import WarehouseOcr


class ShopQuantityOcr:
    def __init__(self, model_type='mobile'):
        self.model_type = model_type

    @staticmethod
    def parse_text(text: str) -> Optional[Tuple[int, int]]:
        text = unicodedata.normalize('NFKC', text).replace('\\', '/')
        text = re.sub(r'\s+', '', text).translate(WarehouseOcr.DIGIT_REPLACEMENTS)
        match = re.fullmatch(r'(\d{1,2})/(\d{1,2})', text)
        if match is None:
            return None
        current, maximum = map(int, match.groups())
        if not 0 <= current <= maximum <= 10:
            return None
        return current, maximum

    def ocr(self, image: np.ndarray) -> Optional[Tuple[int, int]]:
        if image is None or image.size == 0:
            return None
        gray = image.min(axis=2) if image.ndim == 3 else image
        _, labels, stats, _ = cv2.connectedComponentsWithStats((gray < 160).astype(np.uint8))
        max_height = max(stats[1:, cv2.CC_STAT_HEIGHT], default=0)
        # 左侧可能混入被裁断的“剩余”短笔画，不能把它们并入数字行。
        valid = [
            index
            for index, stat in enumerate(stats)
            if index and not (stat[cv2.CC_STAT_LEFT] == 0 and stat[cv2.CC_STAT_HEIGHT] < max_height * 0.5)
        ]
        ys, xs = np.where(np.isin(labels, valid))
        if not len(xs):
            return None
        line = gray[max(0, ys.min() - 2) : ys.max() + 3, max(0, xs.min() - 2) : xs.max() + 3]
        # 与仓库数量识别一样保留灰度笔画并横向拉伸，避免窄字体的斜线在二值化后丢失。
        line = np.clip((line.astype(np.float32) - 55) * 255 / 190, 0, 255).astype(np.uint8)
        model = OCR_MODEL.get_model_by(lang='ch', model_type=self.model_type)
        values = []
        for stretch in (2, 2.5):
            scaled = cv2.resize(line, None, fx=3 * stretch, fy=3, interpolation=cv2.INTER_CUBIC)
            scaled = cv2.copyMakeBorder(scaled, 8, 8, 12, 12, cv2.BORDER_CONSTANT, value=255)
            results = model.recognize([cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGR)])
            if not results:
                return None
            text = results[0].get('rec_text', '')
            score = float(results[0].get('rec_score', 0))
            value = self.parse_text(text)
            if value is None or not math.isfinite(score) or score < 0.85:
                logger.warning(f'BodyLabelShop: unreadable quantity {text!r}, confidence={score:.3f}')
                return None
            values.append(value)
        if values[0] != values[1]:
            logger.warning(f'BodyLabelShop: inconsistent quantity recognition: {values}')
            return None
        return values[0]
