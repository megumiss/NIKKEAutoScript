import math
import re
import unicodedata
from decimal import Decimal
from typing import Optional

import cv2
import numpy as np

from module.logger import logger
from module.ocr.models import OCR_MODEL


class WarehouseOcr:
    DIGIT_REPLACEMENTS = str.maketrans({'O': '0', 'Q': '0', 'D': '0', 'I': '1', 'l': '1', '|': '1', 'S': '5', 'B': '8'})

    def __init__(self, model_type='mobile'):
        self.model_type = model_type

    @staticmethod
    def _extract_line(image: np.ndarray, threshold: int) -> Optional[np.ndarray]:
        height, width = image.shape[:2]
        core = image.min(axis=2) if image.ndim == 3 else image
        _, labels, stats, _ = cv2.connectedComponentsWithStats((core > threshold).astype(np.uint8))
        candidates = [
            (index, stat)
            for index, stat in enumerate(stats)
            if index
            and stat[1] > 0
            and height - 14 <= stat[1] + stat[3] <= height - 2
            and stat[2] < width * 0.5
            and stat[3] >= 2
        ]
        anchors = [(index, stat) for index, stat in candidates if stat[0] + stat[2] > width - 16 and stat[3] >= 7]
        if not anchors:
            return None

        # 数量在格子右下角且基线对齐；排除与裁剪上边界相连的图标/浅色底板。
        _, seed = max(anchors, key=lambda entry: entry[1][0] + entry[1][2])
        top, bottom = seed[1], seed[1] + seed[3]
        left, right = seed[0], seed[0] + seed[2]
        candidates = [(index, stat) for index, stat in candidates if abs(stat[1] + stat[3] - bottom) <= 3]
        for _, stat in sorted(candidates, key=lambda entry: entry[1][0], reverse=True):
            if stat[0] > right:
                continue
            if left - (stat[0] + stat[2]) > 6:
                break
            left, top = min(left, stat[0]), min(top, stat[1])

        # 保留文字框中的断开笔画（如斜线和细数字），用灰度恢复抗锯齿，不能硬二值化。
        valid = [
            index
            for index, stat in enumerate(stats)
            if index and stat[1] > 0 and stat[0] + stat[2] >= left and stat[0] <= right and stat[1] + stat[3] >= top
        ]
        mask = cv2.dilate(np.isin(labels, valid).astype(np.uint8), np.ones((3, 3), dtype=np.uint8))
        gray = 255 - np.clip((core.astype(np.float32) - 127) * 255 / 128, 0, 255).astype(np.uint8)
        gray[mask == 0] = 255
        first = min(candidates, key=lambda entry: entry[1][0])[1]
        if first[0] == left and first[3] < (bottom - top) * 0.65:
            # x 比数字矮；其上方是图标而非数字笔画，保留它会干扰 x1 等短数量。
            gray[: max(0, first[1] - 1), : first[0] + first[2]] = 255
        return gray[max(0, top - 1) : min(height, bottom + 2), max(0, left - 1) : min(width, right + 2)]

    @classmethod
    def parse_text(cls, text: str, expect_ratio: bool = False) -> Optional[int]:
        text = unicodedata.normalize('NFKC', text).replace('×', 'x').replace('\\', '/')
        text = re.sub(r'\s+', '', text).translate(cls.DIGIT_REPLACEMENTS)
        # 不从任意文字中截数字，不猜测缺失的斜线，也不把以 4 开头的数量当成 x 前缀。
        number = r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?'
        pattern = rf'[xX]?({number})([kKmM]?)(?:/([0-9]+))?'
        match = re.fullmatch(pattern, text)
        if match is None or (expect_ratio and match[3] is None):
            return None
        if match[3] is not None and int(match[3]) == 0:
            return None
        unit = match[2].upper()
        if '.' in match[1] and not unit:
            return None
        multiplier = {'': 1, 'K': 1000, 'M': 1000000}[unit]
        return int(Decimal(match[1].replace(',', '')) * multiplier)

    def ocr(self, image: np.ndarray, name: str = 'INVENTORY_ITEM') -> Optional[int]:
        if image.size == 0:
            return None
        line = self._extract_line(image, threshold=180)
        # 进度条上的分数比 x 数量字体更小、更暗，需要保留较暗的斜线笔画。
        ratio = line is None or line.shape[0] <= 17
        if ratio:
            line = self._extract_line(image, threshold=150)
        if line is None:
            logger.debug(f'WarehouseStats: [CountOCR] no quantity line for {name}')
            return None

        model = OCR_MODEL.get_model_by(lang='ch', model_type=self.model_type)
        for stretch in (1.5, 1) if ratio else (2.5, 1.5):
            scaled = cv2.resize(line, None, fx=2 * stretch, fy=2, interpolation=cv2.INTER_CUBIC)
            scaled = cv2.copyMakeBorder(scaled, 6, 6, 10, 10, cv2.BORDER_CONSTANT, value=255)
            result = model.recognize([cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGR)])[0]
            score = float(result.get('rec_score', 0))
            text = result.get('rec_text', '')
            value = self.parse_text(text, expect_ratio=ratio)
            if value is not None and math.isfinite(score) and score >= 0.51:
                logger.debug(f'WarehouseStats: [CountOCR] {name}={value}, text={text!r}, confidence={score:.3f}')
                return value
        logger.warning(f'WarehouseStats: [CountOCR] unreadable {name}, text={text!r}, confidence={score:.3f}')
        return None
