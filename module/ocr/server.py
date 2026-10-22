import threading

from module.ocr.models import OcrModel
from module.ocr.remote_protocol import slim_lines, slim_pages


class SharedOcrServer:
    """托管在 manager 中的 OCR 服务，统一持有本地模型和 ADB 辅助进程。"""

    def __init__(self):
        self._models = OcrModel(use_sidecar=False)
        self._remote_models = {}
        # Paddle 推理和远程连接都不支持并发使用；生成器也必须在锁内消费。
        self._lock = threading.Lock()
        self._closed = False

    def _get_model(self, lang, model_type, command):
        if lang == 'num':
            lang = 'en'
        if lang not in ('ch', 'en'):
            raise ValueError(f'Unsupported lang: {lang}')
        if not command:
            return self._models.get_model_by(lang=lang, model_type=model_type, interval=0)
        key = (tuple(command), lang, model_type)
        if key not in self._remote_models:
            from module.ocr.remote import RemoteOcr

            self._remote_models[key] = RemoteOcr(
                lang, model_type, 0, key[0], local_factory=self._models.get_model_by,
            )
        return self._remote_models[key]

    def predict(self, lang, model_type, images, command=None):
        with self._lock:
            if self._closed:
                raise RuntimeError('Shared OCR service has shut down')
            return slim_pages(self._get_model(lang, model_type, command).predict(images))

    def recognize(self, lang, model_type, images, command=None):
        with self._lock:
            if self._closed:
                raise RuntimeError('Shared OCR service has shut down')
            return slim_lines(self._get_model(lang, model_type, command).recognize(images))

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            from module.ocr.remote import stop_sidecars

            for model in self._remote_models.values():
                model.close()
            stop_sidecars()
            self._remote_models.clear()
            self._models._paddle_cache.clear()
            self._models._paddle_num_cache.clear()


_shared_ocr_server = None
_shared_ocr_lock = threading.Lock()


def get_shared_ocr_server():
    """Manager 的各客户端取得同一服务，模型在首次请求时才加载。"""
    global _shared_ocr_server
    with _shared_ocr_lock:
        if _shared_ocr_server is None:
            _shared_ocr_server = SharedOcrServer()
        return _shared_ocr_server
