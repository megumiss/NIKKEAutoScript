"""
Low-rate screen preview sampled by the Web process, independent of the task.

The worker publishes a frame only when it takes a task screenshot, so the
preview froze whenever a task spent tens of seconds in OCR. When the game runs
on the virtual-display bridge, the Web process can read the bridge's latest
frame itself through the port the worker already forwarded. Reads never touch
the worker's `device.image` or recognition state.

Sampling runs only while a viewer holds a lease (renewed by every
`GET /api/{name}/screenshot`), shares one thread across all viewers, and keeps
a single request in flight.
"""
import threading
import time

import cv2

from module.device.adb import virtual_display_client as vd_client
from module.logger import logger

LEASE_SECONDS = 3.0
SAMPLE_INTERVAL = 1.0
MAX_BACKOFF = 5.0
REQUEST_TIMEOUT = 2.0
THUMB_FACTOR = 2
JPEG_QUALITY = 70


class PreviewSampler:
    def __init__(self, manager):
        """
        Args:
            manager (ProcessManager): Provides `alive`, `preview_source` and `preview_wanted`.
        """
        self._manager = manager
        self._lock = threading.Lock()
        self._lease_until = 0.0
        self._thread = None
        self._thumb_port = None
        self._thumb_supported = False
        # (captured_at, jpeg bytes) or None; replaced atomically.
        self.latest = None

    def touch(self):
        """Renew the viewer lease, starting the shared sampler if needed."""
        with self._lock:
            self._lease_until = time.time() + LEASE_SECONDS
            if self._thread is not None:
                return
            self._set_wanted(True)
            self._thread = threading.Thread(target=self._run, name='preview-sampler', daemon=True)
            self._thread.start()

    def _set_wanted(self, value):
        # Called under self._lock so a new lease cannot be cleared by an exiting thread.
        wanted = getattr(self._manager, 'preview_wanted', None)
        if wanted is None:
            return
        try:
            if value:
                wanted.set()
            else:
                wanted.clear()
        except (EOFError, OSError) as e:
            logger.warning(f'Preview viewer flag unavailable: {e}')

    def _run(self):
        delay = SAMPLE_INTERVAL
        failing = False
        while True:
            with self._lock:
                if time.time() >= self._lease_until:
                    self._thread = None
                    self._set_wanted(False)
                    self.latest = None
                    return
            started = time.time()
            try:
                frame = self._sample()
                if frame is not None:
                    self.latest = frame
                if failing:
                    logger.info('Preview sampler recovered')
                failing = False
                delay = SAMPLE_INTERVAL
            except (OSError, ValueError, vd_client.ScreenshotSizeError) as e:
                # Worker restart, ADB disconnect or a stale forward: keep the
                # task-screenshot preview and retry with backoff.
                if not failing:
                    logger.warning(f'Preview sampler unavailable: {e}')
                failing = True
                delay = min(delay * 2, MAX_BACKOFF)
            time.sleep(max(0.0, started + delay - time.time()))

    def _bridge(self):
        manager = self._manager
        if not manager.alive:
            return None
        source = manager.preview_source
        if not isinstance(source, dict):
            return None
        bridge = source.get('virtual_display')
        if not isinstance(bridge, dict) or not bridge.get('port'):
            return None
        return bridge

    def _supports_thumb(self, port):
        if self._thumb_port != port:
            info = vd_client.bridge_command(port, b'INFO\n', timeout=REQUEST_TIMEOUT)
            self._thumb_supported = b'caps=' in info and b'thumb' in info.split(b'caps=', 1)[1]
            self._thumb_port = port
        return self._thumb_supported

    def _sample(self):
        bridge = self._bridge()
        if bridge is None:
            return None
        port = int(bridge['port'])
        width = int(bridge.get('width') or 720)
        height = int(bridge.get('height') or 1280)
        rotation = int(bridge.get('rotation') or 0)

        captured_at = time.time()
        if self._supports_thumb(port):
            raw_w, raw_h, data = vd_client.bridge_command(
                port, f'THUMB {THUMB_FACTOR}\n'.encode(), timeout=REQUEST_TIMEOUT
            )
            image = vd_client.normalize_frame(
                vd_client.rgb_bytes_to_image(data, raw_w, raw_h), raw_w, raw_h,
                rotation=rotation,
                configured_width=width // THUMB_FACTOR,
                configured_height=height // THUMB_FACTOR,
            )
        else:
            # Older bridge JARs only serve full RGB frames.
            raw_w, raw_h, data = vd_client.bridge_command(port, b'FRAME\n', timeout=REQUEST_TIMEOUT)
            image = vd_client.normalize_frame(
                vd_client.rgb_bytes_to_image(data, raw_w, raw_h), raw_w, raw_h,
                rotation=rotation, configured_width=width, configured_height=height,
            )
            image = cv2.resize(
                image, (width // THUMB_FACTOR, height // THUMB_FACTOR), interpolation=cv2.INTER_AREA
            )

        ok, encoded = cv2.imencode(
            '.jpg', cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not ok:
            raise ValueError('JPEG encoding failed')
        return captured_at, encoded.tobytes()
