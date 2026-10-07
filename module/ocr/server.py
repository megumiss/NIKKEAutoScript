import threading

from module.ocr.models import OcrModel


class SharedOcrServer:
    """
    托管在 SyncManager 进程中的共享 OCR 推理服务。
    多实例 worker 通过代理访问，PaddleOCR 模型只在服务进程加载一份。
    """

    def __init__(self):
        self._models = OcrModel()
        # paddle 推理非线程安全，多 worker 并发调用时串行化
        self._lock = threading.Lock()

    def predict(self, lang, model_type, interval, images):
        """
        Args:
            lang: 'ch' / 'en' / 'num'
            model_type: 'mobile' / 'server'
            interval: OCR 间隔限制（秒）
            images: np.ndarray 或 list[np.ndarray]

        Returns:
            list[dict]: 每页 {'rec_texts', 'rec_scores', 'rec_boxes'}，
                PaddleX 原生结果对象不保证可 pickle，统一转纯 dict 过 IPC
        """
        with self._lock:
            model = self._models.get_model_by(lang=lang, model_type=model_type, interval=interval)
            result = model.predict(images)

        return [
            {
                'rec_texts': list(page.get('rec_texts', [])),
                'rec_scores': [float(s) for s in page.get('rec_scores', [])],
                'rec_boxes': [b.tolist() if hasattr(b, 'tolist') else list(b) for b in page.get('rec_boxes', [])],
            }
            for page in result
        ]


_shared_ocr_server = None


def get_shared_ocr_server():
    """SyncManager 服务进程内的单例工厂。"""
    global _shared_ocr_server
    if _shared_ocr_server is None:
        _shared_ocr_server = SharedOcrServer()
    return _shared_ocr_server
