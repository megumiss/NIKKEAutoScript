import threading
import time

from module.logger import logger


class OcrInitProgress:
    """
    OCR 初始化期间的心跳进度提示。

    paddle import 与模型加载都是不可拆分的阻塞调用，拿不到真实百分比，
    只能周期性输出已耗时，避免长时间无日志被误认为卡死。
    """

    def __init__(self, message: str, interval: float = 5.0):
        self.message = message
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._start = 0.0

    def __enter__(self):
        self._start = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        logger.info(f'{self.message} done, elapsed {time.time() - self._start:.1f}s')

    def _run(self):
        while not self._stop.wait(self.interval):
            logger.info(f'{self.message}, elapsed {int(time.time() - self._start)}s')
