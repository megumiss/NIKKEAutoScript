import time

from module.logger import logger


class OcrInterval:
    """
    两次 OCR 调用之间的最小间隔；本地 NIKKEOcr 与远程侧车代理共用同一套节流语义。
    """

    def __init__(self, interval: float = 0):
        self.interval = interval
        self.last_time = 0

    def check_interval(self):
        delta = time.time() - self.last_time
        if delta < self.interval:
            logger.debug(f'OCR interval {delta:.2f}s < {self.interval}s, sleeping {self.interval - delta:.2f}s')
            time.sleep(self.interval - delta)
        self.last_time = time.time()
