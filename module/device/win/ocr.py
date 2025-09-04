
from datetime import time

from module.base.timer import Timer
from module.base.utils import float2str, point2str
from module.logger import logger
from module.ocr.ocr import Ocr


class LauncherOcr:
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
        if not hasattr(self, "_ocr_cache"):
            self._ocr_cache = {
                "last_hash": None,
                "last_result": None
            }

        current_hash = hash(self.device.image.tobytes())
        if current_hash != self._ocr_cache["last_hash"]:
            # 重新 OCR
            ocr_instance = Ocr(buttons=[], lang=lang, model_type=self.config.Optimization_OcrModelType)
            self._ocr_cache["last_result"] = ocr_instance.ocr(self.device.image, direct_ocr=True, show_log=False)
            self._ocr_cache["last_hash"] = current_hash
        res = self._ocr_cache["last_result"]

        location = self.device.get_location(text, res)
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
            self.device.click_minitouch(location[0], location[1])
            logger.info(
                'Click %s @ %s %ss' % (
                    point2str(location[0], location[1]), f"'{text}'", float2str(time.time() - start_time))
            )
            return True
        else:
            return False