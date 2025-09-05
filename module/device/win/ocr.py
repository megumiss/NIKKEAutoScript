import time
from typing import Optional, Tuple

import cv2
import numpy as np

from module.base.timer import Timer
from module.base.utils import float2str, point2str
from module.logger import logger
from module.ocr.models import OCR_MODEL
from module.ocr.ocr import Ocr


class LauncherOcr:
    get_location = OCR_MODEL.get_location

    def appear_text(self, text, interval=0, lang='ch') -> bool or tuple:
        if interval:
            if text in self.interval_timer:
                if self.interval_timer[text].limit != interval:
                    self.interval_timer[text] = Timer(interval)
            else:
                self.interval_timer[text] = Timer(interval)
            if not self.interval_timer[text].reached():
                return False

        # OCR 缓存
        if not hasattr(self, '_ocr_cache'):
            self._ocr_cache = {'last_hash': None, 'last_result': None}

        cv2.imwrite('launcher.png', np.array(self.launcher.image))
        current_hash = hash(self.launcher.image.tobytes())
        if current_hash != self._ocr_cache['last_hash']:
            # 重新 OCR
            ocr_instance = Ocr(buttons=[], lang=lang, model_type=self.config.Optimization_OcrModelType)
            self._ocr_cache['last_result'] = ocr_instance.ocr(self.launcher.image, direct_ocr=True, show_log=False)
            self._ocr_cache['last_hash'] = current_hash
        res = self._ocr_cache['last_result']

        print(res)
        location = self.get_location(text, res)
        if location:
            if interval:
                self.interval_timer[text].reset()
            return location
        else:
            return False

    def appear_text_then_click(self, text, interval=0) -> bool:
        start_time = time.time()
        location = self.appear_text(text, interval)
        if location:
            logger.info(
                'Click %s @ %s %ss'
                % (point2str(location[0], location[1]), f"'{text}'", float2str(time.time() - start_time))
            )
            self.click_minitouch(self.launcher, location[0], location[1])
            return True
        else:
            return False

    def ocr_text(self, lang='ch'):
        # OCR 缓存
        if not hasattr(self, '_ocr_cache'):
            self._ocr_cache = {'last_hash': None, 'last_result': None}

        current_hash = hash(self.launcher.image.tobytes())
        if current_hash != self._ocr_cache['last_hash']:
            # 重新 OCR
            ocr_instance = Ocr(buttons=[], lang=lang, model_type=self.config.Optimization_OcrModelType)
            self._ocr_cache['last_result'] = ocr_instance.ocr(self.launcher.image, direct_ocr=True, show_log=False)
            self._ocr_cache['last_hash'] = current_hash

        return self._ocr_cache['last_result']

    def check_extra_fields(self, data, start: str, end: str) -> Tuple[Optional[tuple], Optional[list]]:
        """
        检查 start 和 end 之间是否有且只有一个额外字段
        返回:
            (location, bbox) 如果找到额外字段
            (None, None) 如果没有额外字段
        """
        details = data['details']
        start_index = next((i for i, item in enumerate(details) if item['text'] == start), None)
        end_index = next((i for i, item in enumerate(details) if item['text'] == end), None)

        if start_index is None or end_index is None or start_index >= end_index:
            return None, None

        # 取两个关键字之间的所有元素
        between = details[start_index + 1 : end_index]

        if len(between) == 1:
            field = between[0]
            location = self.get_location(field['text'], data)
            bbox = field['bbox']
            if location:
                return location, bbox

        return None, None
