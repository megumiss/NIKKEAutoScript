import os
import time

import numpy as np
from paddleocr import PaddleOCR

from module.exception import RequestHumanTakeover
from module.logger import logger

from .constant import ModelsPath
from .download import maybe_download

models = {
    'PP-OCRv5_server_rec_infer': 'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0//PP-OCRv5_server_rec_infer.tar',
    'PP-OCRv5_mobile_rec_infer': 'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0//PP-OCRv5_mobile_rec_infer.tar',
    'PP-OCRv5_server_det_infer': 'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_server_det_infer.tar',
    'PP-OCRv5_mobile_det_infer': 'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_det_infer.tar',
}


class NIKKEOcr(PaddleOCR):
    def __init__(
        self,
        lang: str = 'ch',
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_det_thresh=0.3,
        text_det_unclip_ratio=2.0,
        text_rec_score_thresh=0.51,
        rec_model_dir: str = None,
        det_model_dir: str = None,
        interval: float = 0,
        model_type: str = 'mobile',
    ):
        """
        初始化OCR
        """
        logger.hr('PaddleOCR Prepare')

        # 如果没有传入模型路径，根据model_type下载/设置路径
        if rec_model_dir is None:
            rec_model_dir = (
                maybe_download(
                    ModelsPath / 'PP-OCRv5_server_rec_infer',
                    models['PP-OCRv5_server_rec_infer'],
                )
                if model_type == 'server'  # 如果使用服务器模型
                else maybe_download(
                    ModelsPath / 'PP-OCRv5_mobile_rec_infer',
                    models['PP-OCRv5_mobile_rec_infer'],
                )
            )
        if det_model_dir is None:
            det_model_dir = (
                maybe_download(
                    ModelsPath / 'PP-OCRv5_server_det_infer',
                    models['PP-OCRv5_server_det_infer'],
                )
                if model_type == 'server'  # 如果使用服务器模型
                else maybe_download(
                    ModelsPath / 'PP-OCRv5_mobile_det_infer',
                    models['PP-OCRv5_mobile_det_infer'],
                )
            )

        # 检查模型文件是否存在
        self._assert_and_prepare_model_files(rec_model_dir, det_model_dir)
        self.interval = interval
        self.last_time = 0

        # 调用父类 PaddleOCR 的 __init__ 完成模型加载
        logger.info('PaddleOCR Initializing')
        super().__init__(
            ocr_version='PP-OCRv5',
            device='CPU',  # CPU模式
            lang=lang,
            use_doc_orientation_classify=use_doc_orientation_classify,
            use_doc_unwarping=use_doc_unwarping,
            use_textline_orientation=use_textline_orientation,
            text_det_thresh=text_det_thresh,
            text_det_unclip_ratio=text_det_unclip_ratio,
            text_rec_score_thresh=text_rec_score_thresh,
            text_detection_model_name='PP-OCRv5_server_det' if model_type == 'server' else 'PP-OCRv5_mobile_det',
            text_detection_model_dir=det_model_dir,
            text_recognition_model_name='PP-OCRv5_server_rec' if model_type == 'server' else 'PP-OCRv5_mobile_rec',
            text_recognition_model_dir=rec_model_dir,
        )

        logger.info('PaddleOCR prepared')

    def predict(self, img_fp):
        self.check_interval()
        return super().predict(img_fp)

    def check_interval(self):
        delta = time.time() - self.last_time
        if delta < self.interval:
            logger.info(f'OCR interval {delta:.2f}s < {self.interval}s, sleeping {self.interval - delta:.2f}s')
            time.sleep(self.interval - delta)
        self.last_time = time.time()

    def _assert_and_prepare_model_files(self, rec_model_dir, det_model_dir):
        required_files = ['inference.json', 'inference.pdiparams', 'inference.yml']
        file_prepared = True
        missing_files = []

        for f in required_files:
            if not os.path.exists(os.path.join(rec_model_dir, f)):
                file_prepared = False
                missing_files.append(os.path.join(rec_model_dir, f))
            if not os.path.exists(os.path.join(det_model_dir, f)):
                file_prepared = False
                missing_files.append(os.path.join(det_model_dir, f))

        if file_prepared:
            logger.info('PaddleOCR model files download complete')
            return

        logger.warning('OCR model files missing in directories:')
        logger.warning(f'Recognition model dir: {rec_model_dir}')
        logger.warning(f'Detection model dir: {det_model_dir}')
        logger.warning(f'Missing files: {missing_files}')
        logger.critical('Please ensure all required model files exist')
        raise RequestHumanTakeover
