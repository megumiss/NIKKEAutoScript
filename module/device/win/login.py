import time

import cv2
import numpy as np

from module.config.account import load_account
from module.device.win.automation import Automation
from module.device.win.ocr import LauncherOcr
from module.logger import logger


class Login(LauncherOcr, Automation):
    def login(self, skip_first=True):
        account, password = load_account(self.config.config_name)

        quick_login = False
        while 1:
            if skip_first:
                skip_first = False
            else:
                time.sleep(3)

            self.get_resolution(self.launcher)
            super().screenshot(self.launcher)

            # 输入邮箱
            if self.appear_text_then_click('保持登录', interval=1):
                time.sleep(0.3)
                auto_type(account)
                continue

            # 输入密码
            if self.appear_text_then_click('保持登录', interval=1):
                time.sleep(0.3)
                auto_type(password)
                continue

            # 点击保持登录
            if not quick_login and self.appear_text_then_click('保持登录', interval=1):
                quick_login = True
                continue

            # 点击登录
            if self.appear_text_then_click('登录', interval=1):
                continue

def auto_type(text):
    after_alpha = False
    for character in text:
        if character.isalpha():
            after_alpha = True
        else:
            if after_alpha:
                after_alpha = False
                # 切换两下中英文模式，避免中文输入法影响英文输入
                auto.secretly_press_key('shift', wait_time=0.1)
                auto.secretly_press_key('shift', wait_time=0.1)
        auto.secretly_press_key(character, wait_time=0.1)
    if text[-1].isalpha():
        auto.secretly_press_key('shift', wait_time=0.1)
        auto.secretly_press_key('shift', wait_time=0.1)
    time.sleep(2)
