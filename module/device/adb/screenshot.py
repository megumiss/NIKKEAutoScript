from collections import deque
from datetime import datetime
from functools import cached_property
import socket
import struct

import numpy as np

from module.base.timer import Timer
from module.base.utils import image_size
from module.device.adb.method.adb import Adb
from module.device.adb.method.droidcast import DroidCast
from module.device.adb.method.nemu_ipc import NemuIpc


class ScreenshotSizeError(Exception):
    pass


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
        image = np.frombuffer(data, np.uint8).reshape((height, width, 3)).copy()
        return self._normalize_virtual_display_image(image, width, height)

    def _normalize_virtual_display_image(self, image, width=None, height=None):
        """Return the configured portrait canvas from any OEM VD orientation."""
        if width is None or height is None:
            height, width = image.shape[:2]
        rotation = int(getattr(self, '_virtual_display_rotation', 0)) % 4
        configured_width = int(getattr(self, '_virtual_display_width', 720))
        configured_height = int(getattr(self, '_virtual_display_height', 1280))
        if (width, height) == (configured_width, configured_height):
            if rotation == 2:
                return np.rot90(image, 2).copy()
            return image
        if (width, height) == (configured_height, configured_width):
            # A secondary display on a landscape-native ROM may report the
            # dimensions transposed. Rotation 1/3 is authoritative; when the
            # ROM reports 0/2, use the same clockwise quarter-turn fallback.
            quarter_turn = rotation if rotation in (1, 3) else 1
            return np.rot90(image, quarter_turn).copy()
        raise ScreenshotSizeError(
            f'The virtual display returned unsupported size {width}x{height}; '
            f'expected {configured_width}x{configured_height}'
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
        data = bytearray()
        while len(data) < size:
            chunk = stream.recv(size - len(data))
            if not chunk:
                raise ScreenshotSizeError('The virtual-display bridge closed the connection')
            data.extend(chunk)
        return bytes(data)

    def _virtual_display_command(self, command, timeout=10):
        port = getattr(self, '_virtual_capture_port', None)
        if port is None:
            raise ScreenshotSizeError('The virtual-display bridge is not connected')
        with socket.create_connection(('127.0.0.1', port), timeout=timeout) as stream:
            stream.sendall(command)
            if command == b'FRAME\n':
                width, height, size = struct.unpack('>III', self._recv_exact(stream, 12))
                expected = width * height * 3
                if not width or not height or size != expected or size > 20 * 1024 * 1024:
                    raise ScreenshotSizeError(
                        f'The virtual-display bridge returned invalid frame {width}x{height}/{size}'
                    )
                return width, height, self._recv_exact(stream, size)
            return stream.recv(256)

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
