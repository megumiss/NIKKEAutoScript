import numpy as np


class OcrModel:
    def __init__(self):
        self._paddle_cache = {}
        self._paddle_num_cache = {}

    def paddle(self, model_type, interval):
        if model_type not in self._paddle_cache:
            from module.ocr.nikke_ocr import NIKKEOcr

            self._paddle_cache[model_type] = NIKKEOcr(
                lang='ch',
                model_type=model_type,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                interval=interval,
            )
        return self._paddle_cache[model_type]

    def paddle_num(self, model_type, interval):
        if model_type not in self._paddle_num_cache:
            from module.ocr.nikke_ocr import NIKKEOcr

            self._paddle_num_cache[model_type] = NIKKEOcr(
                lang='en',
                model_type=model_type,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_det_thresh=0.1,
                text_det_unclip_ratio=6.0,
                interval=interval,
            )
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
