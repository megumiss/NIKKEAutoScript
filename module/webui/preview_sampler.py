"""
Low-rate screen preview sampled by the Web process, independent of the task.

The worker publishes a frame only when it takes a task screenshot, so the
preview froze whenever a task spent tens of seconds in OCR. When the game runs
on the virtual-display bridge, the Web process can read the bridge's latest
frame itself through the verified backend-owned display session. Reads never touch
the worker's `device.image` or recognition state.

Sampling runs only while a viewer holds a lease (renewed by every
`GET /api/{name}/screenshot`), shares one thread across all viewers, and keeps
a single request in flight. Each viewer reports its polling interval: its lease
covers that interval, and the sampler runs at the fastest active interval, so
slow polling neither loses the sampled frame nor pays for 1 s sampling.
"""
import threading
import time

import cv2

from module.device.adb import virtual_display_client as vd_client
from module.logger import logger

# A viewer polling every N seconds keeps its lease for N + LEASE_GRACE seconds.
LEASE_GRACE = 2.0
SAMPLE_INTERVAL = 1.0
MAX_VIEWER_INTERVAL = 30.0
MAX_BACKOFF = 5.0
REQUEST_TIMEOUT = 2.0
# Downscale factor for sampled frames. 1 keeps the 720x1280 task-screenshot size:
# the preview card is laid out from the image's natural size, and a half-size
# frame would render small and soft. THUMB is used only for factors above 1.
THUMB_FACTOR = 1
JPEG_QUALITY = 70


class PreviewSampler:
    def __init__(self, manager):
        """
        Args:
            manager (ProcessManager): Provides the instance name and viewer event.
        """
        self._manager = manager
        self._lock = threading.Lock()
        # polling interval -> last request time, one entry per cadence in use
        self._viewers = {}
        self._wake = threading.Event()
        self._thread = None
        # (owner, captured_at, jpeg bytes) or None, replaced atomically. Kept when
        # the last viewer leaves; frame() only returns it for the same owner.
        self._latest = None

    @staticmethod
    def _owner(bridge):
        return bridge['identity'], bridge['generation'], int(bridge['port'])

    def frame(self):
        """Return a frame only while its verified display session still exists."""
        latest = self._latest
        if latest is None:
            return None
        owner, captured_at, data = latest
        bridge = self._bridge()
        if bridge is None or owner != self._owner(bridge):
            return None
        return captured_at, data

    def touch(self, interval=SAMPLE_INTERVAL):
        """
        Renew a viewer lease, starting the shared sampler if needed.

        Args:
            interval (float): The viewer's polling interval in seconds.
        """
        interval = min(max(float(interval), SAMPLE_INTERVAL), MAX_VIEWER_INTERVAL)
        with self._lock:
            faster = not self._viewers or interval < min(self._viewers)
            self._viewers[interval] = time.time()
            if self._thread is not None:
                if faster:
                    self._wake.set()
                return
            self._set_wanted(True)
            self._thread = threading.Thread(target=self._run, name='preview-sampler', daemon=True)
            self._thread.start()

    def _active_interval(self, now):
        """Fastest polling interval among unexpired leases, or None. Call under self._lock."""
        for interval, last in list(self._viewers.items()):
            if now - last > interval + LEASE_GRACE:
                del self._viewers[interval]
        return min(self._viewers) if self._viewers else None

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
                interval = self._active_interval(time.time())
                if interval is None:
                    self._thread = None
                    self._set_wanted(False)
                    return
            started = time.time()
            try:
                frame = self._sample()
                if frame is not None:
                    self._latest = frame
                    if failing:
                        logger.info('Preview sampler recovered')
                    failing = False
                # No managed display is not an error; keep the normal cadence.
                delay = interval
            except (OSError, ValueError, vd_client.ScreenshotSizeError) as e:
                # Session replacement, ADB disconnect or a stale forward: keep the
                # task-screenshot preview and retry with backoff.
                if not failing:
                    logger.warning(f'Preview sampler unavailable: {e}')
                failing = True
                delay = max(interval, min(delay * 2, MAX_BACKOFF))
            # A viewer polling faster than the current cadence cuts the wait short.
            self._wake.wait(max(0.0, started + delay - time.time()))
            self._wake.clear()

    def _bridge(self):
        from module.device.adb.virtual_display_session import sessions
        return sessions().preview_source(self._manager.config_name)

    def _sample(self):
        bridge = self._bridge()
        if bridge is None:
            return None
        port = int(bridge['port'])
        # A frame read during a session replacement must not reach its new owner.
        owner = self._owner(bridge)
        width = int(bridge.get('width') or 720)
        height = int(bridge.get('height') or 1280)
        rotation = int(bridge.get('rotation') or 0)

        info = vd_client.bridge_command(port, b'INFO\n', timeout=REQUEST_TIMEOUT)
        fields = dict(part.split('=', 1) for part in info.decode('utf-8').split() if '=' in part)
        if fields.get('identity') != bridge['identity'] or fields.get('socket') != bridge['socket']:
            raise vd_client.ScreenshotSizeError('Preview bridge identity changed')
        captured_at = time.time()
        if THUMB_FACTOR > 1 and 'thumb' in fields.get('caps', '').split(','):
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
            # Full-size frames, also the path for older bridge JARs without THUMB.
            raw_w, raw_h, data = vd_client.bridge_command(port, b'FRAME\n', timeout=REQUEST_TIMEOUT)
            image = vd_client.normalize_frame(
                vd_client.rgb_bytes_to_image(data, raw_w, raw_h), raw_w, raw_h,
                rotation=rotation, configured_width=width, configured_height=height,
            )
            if THUMB_FACTOR > 1:
                image = cv2.resize(
                    image, (width // THUMB_FACTOR, height // THUMB_FACTOR), interpolation=cv2.INTER_AREA
                )

        ok, encoded = cv2.imencode(
            '.jpg', cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not ok:
            raise ValueError('JPEG encoding failed')
        return owner, captured_at, encoded.tobytes()
