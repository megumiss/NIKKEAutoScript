from functools import cached_property

import numpy as np

from module.base.button import Button
from module.base.utils import ensure_int, point2str
from module.device.method.minitouch import Minitouch
from module.logger import logger


class Control(Minitouch):
    def handle_control_check(self, button):
        # Will be overridden in Device
        pass

    @cached_property
    def click_methods(self):
        return {
            'minitouch': self.click_minitouch,
        }

    def click(self, button: Button, control_check=True):
        """Method to click a button.

        Args:
            button (button.Button): AzurLane Button instance.
            control_check (bool):
        """
        if control_check:
            self.handle_control_check(button)

        # x, y = random_rectangle_point(button.button)
        x, y = button.location

        x, y = ensure_int(x, y)
        logger.info(
            'Click %s @ %s' % (point2str(x, y), button)
        )
        method = self.click_methods.get(
            self.config.Emulator_ControlMethod)
        method(x, y)

    def swipe(self, p1, p2, speed=15, name='SWIPE', label='Swipe', distance_check=True, handle_control_check=True):
        if handle_control_check:
            self.handle_control_check(name)
        p1, p2 = ensure_int(p1, p2)
        method = self.config.Emulator_ControlMethod
        if method == 'minitouch':
            logger.info('%s %s -> %s' % (label, point2str(*p1), point2str(*p2)))

        if distance_check:
            if np.linalg.norm(np.subtract(p1, p2)) < 10:
                # Should swipe a certain distance, otherwise AL will treat it as click.
                # uiautomator2 should >= 6px, minitouch should >= 5px
                logger.info('Swipe distance < 10px, dropped')
                return

        if method == 'minitouch':
            self.swipe_minitouch(p1, p2, speed=speed)
