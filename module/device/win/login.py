import time

import cv2
import numpy as np

from module.config.account import load_account
from module.device.win.automation import Automation
from module.device.win.ocr import LauncherOcr
from module.logger import logger


class Login(LauncherOcr, Automation):
    def login(self, skip_first=True):
        while 1:
            if skip_first:
                skip_first = False
            else:
                time.sleep(3)

            self.get_resolution(self.launcher)
            super().screenshot(self.launcher)

            if self.appear_text_then_click('保持登录', interval=1):
                # button = Button(area=location, name='保持登录')
                # self.click(self.launcher, button)
                logger.info('点击保持登录')
                continue

        cv2.imwrite('launcher.png', np.array(self.launcher.image))

        print(load_account(self.config.config_name))
        print(self.nikke_name)
