import json

import numpy as np

from module.config.utils import filepath_config
from module.logger import logger
from module.ocr.progress import OcrInitProgress


def get_ocr_cpu_threads() -> int:
    """
    从配置文件读取 OCR 线程数设置（NKAS.Optimization.OcrThreads），
    缺失、非法或非正数时回退为默认值 10。
    """
    try:
        with open(filepath_config('nkas'), encoding='utf-8') as f:
            data = json.load(f)
        threads = int(data.get('NKAS', {}).get('Optimization', {}).get('OcrThreads', 10) or 10)
        return threads if threads > 0 else 10
    except (OSError, ValueError, TypeError) as e:
        logger.warning(f'Failed to read Optimization.OcrThreads from config: {e}')
        return 10


def get_ocr_device() -> str:
    """
    从 deploy.yaml 读取 OCR 设备（OcrDevice: cpu/gpu），缺失或非法时回退为 cpu。
    GPU 包是否安装由 deploy/pip.py 在启动时保证，这里只按配置透传，不做可用性回退。
    """
    from deploy.utils import DEPLOY_CONFIG, poor_yaml_read

    try:
        device = str(poor_yaml_read(DEPLOY_CONFIG).get('OcrDevice', 'cpu') or 'cpu').strip().lower()
        return device if device in ('cpu', 'gpu') else 'cpu'
    except (OSError, ValueError, TypeError) as e:
        logger.warning(f'Failed to read OcrDevice from deploy config: {e}')
        return 'cpu'


def paddle_options(lang, model_type, interval=0):
    """
    NIKKEOcr 构造参数的唯一来源；dev_tools/ocr_benchmark.py 复用它，保证基准与运行时一致。

    Args:
        lang (str): 'ch' 或 'en'（数字模型）
    """
    options = dict(
        lang=lang,
        model_type=model_type,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        interval=interval,
        device=get_ocr_device(),
        cpu_threads=get_ocr_cpu_threads(),
    )
    if lang == 'en':
        options.update(text_det_thresh=0.1, text_det_unclip_ratio=6.0)
    return options


def get_ocr_sidecar_enabled() -> bool:
    """
    从 deploy.yaml 读取 OCR 侧车开关（OcrSidecar），缺失或非法时为关闭。
    """
    from deploy.utils import DEPLOY_CONFIG, poor_yaml_read

    try:
        return poor_yaml_read(DEPLOY_CONFIG).get('OcrSidecar', False) is True
    except (OSError, ValueError, TypeError) as e:
        logger.warning(f'Failed to read OcrSidecar from deploy config: {e}')
        return False


def create_local_model(lang, model_type, interval=0):
    # 首次 import paddle 需数十秒，包一层心跳日志避免看起来像卡死
    with OcrInitProgress('Importing paddle library'):
        from module.ocr.nikke_ocr import NIKKEOcr

    return NIKKEOcr(**paddle_options(lang, model_type, interval))


class OcrModel:
    def __init__(self, use_sidecar=True):
        """
        Args:
            use_sidecar (bool): False 时始终在本进程加载模型（侧车自身使用，避免递归代理）。
        """
        self._paddle_cache = {}
        self._paddle_num_cache = {}
        self._use_sidecar = use_sidecar
        self._sidecar_unavailable_logged = False

    def _sidecar_command(self):
        if not self._use_sidecar or not get_ocr_sidecar_enabled():
            return None
        from module.ocr.sidecar_launch import termux_sidecar_command

        command, reason = termux_sidecar_command()
        if command is None and not self._sidecar_unavailable_logged:
            logger.warning(f'OcrSidecar is on but unavailable here ({reason}), OCR runs in this process')
            self._sidecar_unavailable_logged = True
        return command

    def _create(self, lang, model_type, interval):
        command = self._sidecar_command()
        if command:
            from module.ocr.remote import RemoteOcr

            return RemoteOcr(lang, model_type, interval, command)
        return create_local_model(lang, model_type, interval)

    def paddle(self, model_type, interval):
        if model_type not in self._paddle_cache:
            self._paddle_cache[model_type] = self._create('ch', model_type, interval)
        return self._paddle_cache[model_type]

    def paddle_num(self, model_type, interval):
        if model_type not in self._paddle_num_cache:
            self._paddle_num_cache[model_type] = self._create('en', model_type, interval)
        return self._paddle_num_cache[model_type]

    def get_model_by(self, lang='ch', model_type='mobile', interval=0):
        if lang == 'ch':
            return self.paddle(model_type=model_type, interval=interval)
        elif lang in ('en', 'num'):
            return self.paddle_num(model_type=model_type, interval=interval)
        else:
            raise ValueError(f'Unsupported lang: {lang}')

    def get_location(self, text, result, threshold=0.7):
        """
        获取目标文本在 OCR 结果中的中心坐标

        Args:
            text: 要查找的目标文本
            result: _process_ocr_result 返回的结果字典
            threshold: 匹配相似度阈值 (0~1)

        Returns:
            tuple: (x, y) 中心坐标，未找到返回 None
        """
        if not result or not result.get('details'):
            return None

        # 构建文本到 bbox 的映射
        text_bbox_map = {item['text']: item['bbox'] for item in result['details']}
        all_texts = list(text_bbox_map.keys())

        # 找到最相似的文本
        ratio, matched_text = self.get_similarity(all_texts, text)
        if not (ratio >= threshold and matched_text in text_bbox_map):
            return None

        raw_bbox = text_bbox_map[matched_text]
        if raw_bbox is None:
            return None

        # 转成 numpy array 方便判断维度
        bbox = np.array(raw_bbox)

        # bbox 可能两种形态：
        # 1) 4×2 的点阵：[[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
        # 2) 1×4 的平铺：[x1, y1, x2, y2]
        if bbox.ndim == 2 and bbox.shape == (4, 2):
            ul = bbox[0]  # 左上
            br = bbox[2]  # 右下
        elif bbox.ndim == 1 and bbox.size == 4:
            ul = bbox[:2]  # [x1, y1]
            br = bbox[2:4]  # [x2, y2]
        else:
            return None

        # 计算中心点
        x = (int(ul[0]) + int(br[0])) / 2
        y = (int(ul[1]) + int(br[1])) / 2
        return x, y

    def get_similarity(self, texts, target, threshold=0.49):
        """计算文本相似度

        Args:
            texts: 候选文本列表
            target: 目标文本
            threshold: 相似度阈值

        Returns:
            tuple: (相似度, 最匹配的文本)
        """
        import difflib

        # 处理目标文本中的下划线
        clean_target = target.strip('_')

        max_ratio = 0
        most_matched = ''

        for text in texts:
            # 下划线特殊处理
            if '_' in target and clean_target == text:
                return 1.0, text  # 完全匹配

            ratio = difflib.SequenceMatcher(None, text, target).ratio()
            if ratio > max_ratio:
                max_ratio = ratio
                most_matched = text

        # 返回超过阈值的结果
        return (max_ratio, most_matched) if max_ratio >= threshold else (0, '')


OCR_MODEL = OcrModel()
