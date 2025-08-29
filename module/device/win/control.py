from functools import cached_property

import numpy as np

from module.base.button import Button
from module.base.utils import ensure_int, point2str
from module.logger import logger


class Control():
    def handle_control_check(self, button):
        # Will be overridden in Device
        pass


