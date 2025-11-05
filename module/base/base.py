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

    def __init__(self, config, device=None, task=None, independent=False):
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
            if task is not None:
                self.config.init_task(task)
        elif isinstance(config, str):
            self.config = NikkeConfig(config, task=task)
        else:
            logger.warning('NKAS ModuleBase received an unknown config, assume it is NikkeConfig')
            self.config = config

        if config.CLIENT_PLATFORM == 'adb':
            from module.device.adb.device import Device as DeviceClass
        if config.CLIENT_PLATFORM == 'win':
            from module.device.win.device import Device as DeviceClass

        # 妮游社任务不需要device
        if not independent:
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

    def appear_any(self, buttons, **kwargs):
        """任意一个按钮出现即返回 True"""
        for btn in buttons:
            if self.appear(btn, **kwargs):
                return True
        return False

    def appear_then_click_any(self, buttons, **kwargs):
        """任意一个按钮出现即点击并返回 True"""
        for btn in buttons:
            if self.appear_then_click(btn, **kwargs):
                return True
        return False

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

    def appear_location(self, button: Button, offset=0, threshold=None, static=True):
        """
        查找按钮在屏幕中的位置并返回坐标（左上角x, 左上角y, 右下角x, 右下角y）

        Returns:
            tuple[int, int, int, int] | None: 找到则返回坐标，否则返回 None
        """
        self.device.stuck_record_add(button)

        if offset:
            if isinstance(offset, bool):
                offset = self.config.BUTTON_OFFSET

            appear = button.match(
                self.device.image,
                offset=offset,
                threshold=self.config.BUTTON_MATCH_SIMILARITY if not threshold else threshold,
                static=static
            )
        else:
            appear = button.appear_on(
                self.device.image,
                threshold=self.config.COLOR_SIMILAR_THRESHOLD if not threshold else threshold
            )

        if appear:
            # match 成功后，button._button_offset 已被更新
            if hasattr(button, "_button_offset"):
                logger.info(f"Button '{button.name}' found at {button._button_offset}")
                x1, y1, x2, y2 = button._button_offset
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                return cx, cy
            else:
                logger.warning(f"Button '{button.name}' matched but no offset recorded")
                return None
        else:
            logger.info(f"Button '{button.name}' not found")
            return None

    def appear_then_click(self, button, offset=0, click_offset=0, interval=0, threshold=None,
                          static=True, screenshot=False) -> bool:

        appear = self.appear(button, offset=offset, interval=interval, threshold=threshold, static=static)
        if appear:
            if screenshot:
                self.device.sleep(self.config.WAIT_BEFORE_SAVING_SCREEN_SHOT)
                self.device.screenshot()
            self.device.click(button, click_offset)

        return appear

    def appear_with_scale(self, button: Button, interval=0, threshold=None, scale_range=(0.9, 1.1), scale_step=0.02) -> bool:
        self.device.stuck_record_add(button)

        if interval:
            if button.name in self.interval_timer:
                if self.interval_timer[button.name].limit != interval:
                    self.interval_timer[button.name] = Timer(interval)
            else:
                self.interval_timer[button.name] = Timer(interval)

            if not self.interval_timer[button.name].reached():
                return False

        appear = button.match_with_scale(
            self.device.image,
            threshold=self.config.BUTTON_MATCH_SIMILARITY if threshold is None else threshold,
            scale_range=scale_range,
            scale_step=scale_step
        )

        if appear and interval:
            self.interval_timer[button.name].reset()

        return appear

    def appear_with_scale_then_click(self, button, click_offset=0, interval=0, threshold=None,
                          scale_range=(0.9, 1.1), scale_step=0.02, screenshot=False) -> bool:

        appear = self.appear_with_scale(button, interval=interval, threshold=threshold, scale_range=scale_range, scale_step=scale_step)
        if appear:
            if screenshot:
                self.device.sleep(self.config.WAIT_BEFORE_SAVING_SCREEN_SHOT)
                self.device.screenshot()
            self.device.click(button, click_offset)

        return appear

    def appear_text(self, text, threshold=0.7, interval=0, lang='ch') -> bool or tuple:
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

        location = self.device.get_location(text, res, threshold=threshold)
        if location:
            if interval:
                self.interval_timer[text].reset()
            return location
        else:
            return False

    def appear_text_then_click(self, text, threshold=0.7, interval=0) -> bool:
        """
        检测指定文本是否出现在画面上，并点击其中心坐标

        Args:
            text: 要检测并点击的目标文本
            threshold: 匹配相似度阈值 (0~1)
            interval: 检测间隔限制（秒），0 表示不限制

        Returns:
            bool: 点击成功返回 True，否则返回 False
        """
        start_time = time.time()
        location = self.appear_text(text, threshold=threshold, interval=interval)
        if location:
            self.device.click_minitouch(location[0], location[1])
            logger.info(
                "Click %s @ %s %ss" % (
                    point2str(location[0], location[1]),
                    f"'{text}'",
                    float2str(time.time() - start_time)
                )
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

    def ensure_sroll(self, x1=(360, 460), x2=(360, 900), speed=15, count=2, delay=1.5, method='scroll'):
        """
        对于adb:
        method: scroll, 平滑滑动
        method: swipe, 快速滑动
        对于PC:
        method: scroll, 使用鼠标滚轮滚动
        method: swipe, 使用鼠标左键快速滑动
        """
        for i in range(count):
            self.device.swipe(x1, x2, speed=speed, method=method, handle_control_check=False)
            self.device.sleep(delay)

    def ensure_sroll_to_top(self, x1=(360, 460), x2=(360, 900), speed=30, count=2, delay=1.5):
        for i in range(count):
            self.device.swipe(x1, x2, method='swipe', speed=speed, handle_control_check=False)
            self.device.sleep(delay)

    def ensure_sroll_to_bottom(self, x1=(360, 900), x2=(360, 460), speed=30, count=2, delay=1.5):
        for i in range(count):
            self.device.swipe(x1, x2, method='swipe', speed=speed, handle_control_check=False)
            self.device.sleep(delay)
