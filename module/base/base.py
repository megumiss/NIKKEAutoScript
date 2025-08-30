import time
from functools import cached_property

import numpy as np

from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import float2str, point2str
from module.config.config import NikkeConfig
from module.logger import logger
from module.ocr.models import OCR_MODEL
from module.ocr.ocr import Ocr


class ModuleBase:
    config: NikkeConfig

    def __init__(self, config, device=None, task=None):
        """
              Args:
                  config (NikkeConfig, str):
                      Name of the user config under ./config
                  device (Device):
                      To reuse a device.
                      If None, create a new Device object.
                      If str, create a new Device object and use the given device as serial.
                  task (str):
                      Bind a task only for dev purpose. Usually to be None for auto task scheduling.
                      If None, use default configs.
              """
        if isinstance(config, NikkeConfig):
            self.config = config
        elif isinstance(config, str):
            self.config = NikkeConfig(config, task=task)
        else:
            logger.warning('NKAS ModuleBase received an unknown config, assume it is NikkeConfig')
            self.config = config

        if config.CLIENT_PLATFORM == 'adb':
            from module.device.adb.device import Device as DeviceClass
        if config.CLIENT_PLATFORM == 'win':
            from module.device.win.device import Device as DeviceClass

        if isinstance(device, DeviceClass):
            self.device = device
        elif device is None:
            self.device = DeviceClass(config=self.config)
        elif isinstance(device, str):
            self.config.override(Emulator_Serial=device)
            self.device = DeviceClass(config=self.config)
        else:
            logger.warning('NKAS ModuleBase received an unknown device, assume it is Device')
            self.device = device

        self.interval_timer = {}

    @cached_property
    def ocr_models(self):
        return OCR_MODEL

    def appear(self, button: Button, offset=0, interval=0, threshold=None, static=True) -> bool:

        self.device.stuck_record_add(button)

        if interval:
            if button.name in self.interval_timer:
                if self.interval_timer[button.name].limit != interval:
                    self.interval_timer[button.name] = Timer(interval)
            else:
                self.interval_timer[button.name] = Timer(interval)
            if not self.interval_timer[button.name].reached():
                return False

        if offset:
            if isinstance(offset, bool):
                offset = self.config.BUTTON_OFFSET

            appear = button.match(self.device.image, offset=offset,
                                  threshold=self.config.BUTTON_MATCH_SIMILARITY if not threshold else threshold,
                                  static=static)
        else:
            appear = button.appear_on(self.device.image,
                                      threshold=self.config.COLOR_SIMILAR_THRESHOLD if not threshold else threshold)

        if appear and interval:
            self.interval_timer[button.name].reset()

        return appear

    def appear_then_click(self, button, offset=0, click_offset=0, interval=0, threshold=None,
                          static=True, screenshot=False) -> bool:

        appear = self.appear(button, offset=offset, interval=interval, threshold=threshold, static=static)
        if appear:
            if screenshot:
                self.device.sleep(self.config.WAIT_BEFORE_SAVING_SCREEN_SHOT)
                self.device.screenshot()
            self.device.click(button, click_offset)

        return appear

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

    def _appear_text_then_click(self, text, location, label, interval=0, model_type='mobile') -> bool:
        start_time = time.time()
        _ = self.appear_text(text, interval, model_type=model_type)
        if _:
            self.device.click_minitouch(location[0], location[1])
            logger.info(
                'Click %s @ %s %ss' % (
                    point2str(location[0], location[1]), f"{label}", float2str(time.time() - start_time))
            )
            return True
        else:
            return False

    def interval_reset(self, button):
        if isinstance(button, (list, tuple)):
            for b in button:
                self.interval_reset(b)
            return

        if button.name in self.interval_timer:
            self.interval_timer[button.name].reset()
        # else:
        #     self.interval_timer[button.name] = Timer(3).reset()

    def ensure_sroll(self, x1=(360, 460), x2=(360, 900), speed=15, count=2, delay=1.5, hold=0, method='swipe'):
        for i in range(count):
            self.device.swipe(x1, x2, speed=speed, hold=hold, method=method, handle_control_check=False)
            self.device.sleep(delay)

    def ensure_sroll_to_top(self, x1=(360, 460), x2=(360, 900), speed=30, count=2, delay=1.5):
        for i in range(count):
            self.device.swipe(x1, x2, speed=speed, handle_control_check=False)
            self.device.sleep(delay)

    def ensure_sroll_to_bottom(self, x1=(360, 900), x2=(360, 460), speed=30, count=2, delay=1.5):
        for i in range(count):
            self.device.swipe(x1, x2, speed=speed, handle_control_check=False)
            self.device.sleep(delay)
