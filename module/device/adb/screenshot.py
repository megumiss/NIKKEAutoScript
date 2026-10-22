from collections import deque
from datetime import datetime
from functools import cached_property

from module.base.timer import Timer
from module.base.utils import image_size
from module.device.adb import virtual_display_client as vd_client
from module.device.adb.method.adb import Adb
from module.device.adb.method.droidcast import DroidCast
from module.device.adb.method.nemu_ipc import NemuIpc
from module.device.adb.virtual_display_client import ScreenshotSizeError


class Screenshot(DroidCast, NemuIpc, Adb):
    def __init__(self, config):
        super().__init__(config)
        self._screenshot_interval = Timer(
            float(self.config.Emulator_ScreenshotInterval)
        )

    @cached_property
    def screenshot_methods(self):
        return {
            "DroidCast": self.screenshot_droidcast_raw,
            "ADB": self.screenshot_adb,
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

        if getattr(self, '_virtual_display_id', None) is not None:
            self.image = self.screenshot_virtual_display()
        else:
            method = self.screenshot_methods.get(self.config.Emulator_ScreenshotMethod)
            self.image = method()

        self.image = self._handle_orientated_image(self.image)

        self.screenshot_deque.append({"time": datetime.now(), "image": self.image})

        return self.image

    def screenshot_virtual_display(self):
        width, height, data = self._virtual_display_command(b'FRAME\n')
        self._virtual_display_raw_width = width
        self._virtual_display_raw_height = height
        # The bridge already follows NKAS's RGB screenshot convention.
        image = vd_client.rgb_bytes_to_image(data, width, height)
        return self._normalize_virtual_display_image(image, width, height)

    def _normalize_virtual_display_image(self, image, width=None, height=None):
        """Return the configured portrait canvas from any OEM VD orientation."""
        if width is None or height is None:
            height, width = image.shape[:2]
        return vd_client.normalize_frame(
            image, width, height,
            rotation=getattr(self, '_virtual_display_rotation', 0),
            configured_width=int(getattr(self, '_virtual_display_width', 720)),
            configured_height=int(getattr(self, '_virtual_display_height', 1280)),
        )

    def _virtual_display_transform_point(self, x, y):
        """Map canonical 720x1280 coordinates to the raw ImageReader frame."""
        x = int(round(float(x)))
        y = int(round(float(y)))
        raw_width = int(getattr(self, '_virtual_display_raw_width', 720))
        raw_height = int(getattr(self, '_virtual_display_raw_height', 1280))
        rotation = int(getattr(self, '_virtual_display_rotation', 0)) % 4
        if (raw_width, raw_height) == (720, 1280):
            if rotation == 2:
                return raw_width - 1 - x, raw_height - 1 - y
            return x, y
        if (raw_width, raw_height) == (1280, 720):
            if rotation == 3:
                return y, raw_height - 1 - x
            # Rotation 0/2 is a common vendor reporting quirk; treat it as
            # the same quarter-turn used by frame normalization.
            return raw_width - 1 - y, x
        return x, y

    @staticmethod
    def _recv_exact(stream, size):
        return vd_client.recv_exact(stream, size)

    def _virtual_display_command(self, command, timeout=10):
        return vd_client.bridge_command(
            getattr(self, '_virtual_capture_port', None), command,
            timeout=timeout, recv=self._recv_exact,
        )

    def _handle_orientated_image(self, image):
        """
        Args:
            image (np.ndarray):

        Returns:
            np.ndarray:
        """
        width, height = image_size(image)
        if width == 720 and height == 1280:
            return image

        raise ScreenshotSizeError("The emulator's display size must be 720*1280")
