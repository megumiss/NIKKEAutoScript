from collections import deque
from datetime import datetime
from functools import cached_property

from module.base.timer import Timer
from module.base.utils import image_size
from module.device.method.droidcast import DroidCast
from module.device.method.nemu_ipc import NemuIpc


class ScreenshotSizeError(Exception):
    pass


class Screenshot(DroidCast, NemuIpc):
    def __init__(self, config):
        super().__init__(config)
        self._screenshot_interval = Timer(
            float(self.config.Emulator_ScreenshotInterval)
        )

    @cached_property
    def screenshot_methods(self):
        return {
            "DroidCast": self.screenshot_droidcast_raw,
        }

    @cached_property
    def screenshot_deque(self):
        return deque(maxlen=int(self.config.Error_ScreenshotLength))

    def screenshot(self):
        """
        截图

        Returns:
            np.ndarray:
        """

        # 每次两次截图间隔时间
        self._screenshot_interval.wait()
        self._screenshot_interval.reset()

        method = self.screenshot_methods.get(self.config.Emulator_ScreenshotMethod)
        self.image = method()

        self.image = self._handle_orientated_image(self.image)

        self.screenshot_deque.append({"time": datetime.now(), "image": self.image})

        return self.image

    def _handle_orientated_image(self, image):
        """
        Args:
            image (np.ndarray):

        Returns:
            np.ndarray:
        """
        width, height = image_size(self.image)
        if width == 720 and height == 1280:
            return image

        raise ScreenshotSizeError("The emulator's display size must be 720*1280")
