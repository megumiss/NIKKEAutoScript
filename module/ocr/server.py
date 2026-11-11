import threading
from concurrent.futures import ThreadPoolExecutor

from module.ocr.models import OcrModel
from module.ocr.remote_protocol import slim_lines, slim_pages


class SharedOcrServer:
    """托管在 manager 中的 OCR 服务，统一持有本地模型和 ADB 辅助进程。"""

    def __init__(self):
        self._models = OcrModel(use_sidecar=False)
        self._remote_models = {}
        # Paddle/oneDNN 模型跨线程串行调用也可能失败，创建、推理和释放必须固定在同一线程。
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='shared-ocr')
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

    def _infer(self, op, lang, model_type, images, command):
        result = getattr(self._get_model(lang, model_type, command), op)(images)
        # Paddle 返回的生成器也要在模型所属线程消费。
        return slim_pages(result) if op == 'predict' else slim_lines(result)

    def _call(self, op, lang, model_type, images, command):
        with self._lock:
            if self._closed:
                raise RuntimeError('Shared OCR service has shut down')
            return self._executor.submit(self._infer, op, lang, model_type, images, command).result()

    def predict(self, lang, model_type, images, command=None):
        return self._call('predict', lang, model_type, images, command)

    def recognize(self, lang, model_type, images, command=None):
        return self._call('recognize', lang, model_type, images, command)

    def _close_models(self):
        from module.ocr.remote import stop_sidecars

        for model in self._remote_models.values():
            model.close()
        stop_sidecars()
        self._remote_models.clear()
        self._models._paddle_cache.clear()
        self._models._paddle_num_cache.clear()

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._executor.submit(self._close_models).result()
            finally:
                self._executor.shutdown(wait=True)


_shared_ocr_server = None
_shared_ocr_lock = threading.Lock()


def get_shared_ocr_server():
    """Manager 的各客户端取得同一服务，模型在首次请求时才加载。"""
    global _shared_ocr_server
    with _shared_ocr_lock:
        if _shared_ocr_server is None:
            _shared_ocr_server = SharedOcrServer()
        return _shared_ocr_server
