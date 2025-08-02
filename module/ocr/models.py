from functools import cached_property

import numpy as np


class OcrModel:
    def __init__(self):
        self._paddle_cache = {}
        self._paddle_num_cache = {}

    def paddle(self, model_type):
        if model_type not in self._paddle_cache:
            from module.ocr.nikke_ocr import NIKKEOcr

            self._paddle_cache[model_type] = NIKKEOcr(
                lang='ch',
                model_type=model_type,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        return self._paddle_cache[model_type]

    def paddle_num(self, model_type):
        if model_type not in self._paddle_num_cache:
            from module.ocr.nikke_ocr import NIKKEOcr

            self._paddle_num_cache[model_type] = NIKKEOcr(
                lang='en',
                model_type=model_type,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_det_thresh=0.1,
                text_det_unclip_ratio=5.0,
            )
        return self._paddle_num_cache[model_type]

    def get_model_by(self, lang='ch', model_type='mobile'):
        if lang == 'ch':
            return self.paddle(model_type=model_type)
        elif lang in ('en', 'num'):
            return self.paddle_num(model_type=model_type)
        else:
            raise ValueError(f'Unsupported lang: {lang}')

    def get_location(self, text, result):
        """获取目标文本在OCR结果中的中心坐标

        Args:
            text: 要查找的目标文本
            result: _process_ocr_result返回的结果字典

        Returns:
            tuple: (x, y) 中心坐标，未找到返回None
        """
        if not result or not result[0]['details']:
            return None

        # 创建 {文本: bbox} 的映射字典
        text_bbox_map = {}
        for item in result['details']:
            # 使用bbox作为位置信息
            text_bbox_map[item['text']] = item['bbox']

        # 获取所有文本用于相似度匹配
        all_texts = [item['text'] for item in result['details']]

        # 查找最相似的文本
        ratio, matched_text = self.get_similarity(all_texts, text, threshold=0.51)

        if ratio > 0 and matched_text in text_bbox_map:
            bbox = text_bbox_map[matched_text]

            # 计算中心点 (假设bbox格式为[[x1,y1], [x2,y2], [x3,y3], [x4,y4]])
            if bbox and len(bbox) == 4:
                # 使用左上和右下点计算中心
                upper_left = bbox[0]
                bottom_right = bbox[2]
                x = (upper_left[0] + bottom_right[0]) / 2
                y = (upper_left[1] + bottom_right[1]) / 2
                return x, y
        return None

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
