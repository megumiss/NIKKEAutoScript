import os
import time

import cv2
import numpy as np
from paddleocr import PaddleOCR
from PIL import Image

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
    last_time = time.time()

    def __init__(
        self,
        rec_model_dir: str = None,
        det_model_dir: str = None,
        interval: float = 0,
        server_model: bool = False,
        return_word_box: bool = False,
    ):
        """
        初始化OCR
        :param rec_model_dir:  文本识别模型目录
        :param det_model_dir:  文本检测模型目录
        :param interval:  OCR调用间隔时间
        :param server_model:  是否使用Server模型 Server模型更大
        :param return_word_box:  是否返回单字框
        :return:
        """
        logger.debug('初始化OCR')
        # 由于PaddleOCR的import速度较慢，所以在这里导入
        from paddleocr import PaddleOCR

        if rec_model_dir is None:  # 如果没有传入模型目录，则下载模型
            rec_model_dir = (
                maybe_download(
                    ModelsPath / 'OCRv5_server_rec_infer',
                    models['OCRv5_server_rec_infer'],
                )
                if server_model  # 如果使用服务器模型
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
                if server_model  # 如果使用服务器模型
                else maybe_download(
                    ModelsPath / 'PP-OCRv5_mobile_det_infer',
                    models['PP-OCRv5_mobile_det_infer'],
                )
            )

        self._assert_and_prepare_model_files(rec_model_dir, det_model_dir)
        self.interval = interval

        device = 'CPU'
        logger.debug(f'使用{device}进行OCR识别')
        self.paddleOCR = PaddleOCR(
            use_angle_cls=False,
            lang='ch',
            use_gpu=False,
            show_log=False,
            rec_model_dir=rec_model_dir,
            det_model_dir=det_model_dir,
            return_word_box=return_word_box,
        )
        self.return_word_box = return_word_box
        logger.debug('初始化OCR完成')

    def check_interval(self):
        """
        检查OCR调用间隔
        :return:
        """
        if time.time() - self.last_time < self.interval:
            time.sleep(self.interval - (time.time() - self.last_time))
        self.last_time = time.time()

    def ocr(self, img_fp):
        if self.interval:
            self.check_interval()

        return super().predict(img_fp)

    def __call__(self, img: np.ndarray):
        return self.ocr(img)

    def _assert_and_prepare_model_files(self, rec_model_dir, det_model_dir):
        # 需要检查的模型文件列表
        required_files = ['inference.json', 'inference.pdiparams', 'inference.yml']
        file_prepared = True
        missing_files = []

        # 检查识别模型目录(rec_model_dir)
        for f in required_files:
            file_path = os.path.join(rec_model_dir, f)
            if not os.path.exists(file_path):
                file_prepared = False
                missing_files.append(file_path)

        # 检查检测模型目录(det_model_dir)
        for f in required_files:
            file_path = os.path.join(det_model_dir, f)
            if not os.path.exists(file_path):
                file_prepared = False
                missing_files.append(file_path)

        if file_prepared:
            return

        # 输出详细的错误信息
        logger.warning('OCR model files missing in directories:')
        logger.warning(f'Recognition model dir: {rec_model_dir}')
        logger.warning(f'Detection model dir: {det_model_dir}')
        logger.warning(f'Missing files: {missing_files}')
        logger.critical('Please ensure all required model files exist')
        raise RequestHumanTakeover
