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
        if result:
            merged_dict = {}
            for dictionary in list(map(lambda x: {x['text']: x['position']}, result)):
                merged_dict.update(dictionary)

            r = None
            _, text = self.get_similarity(list(map(lambda x: x['text'], result)), text, threshold=0.51)

            if _:
                r = [merged_dict[text]]

            if r:
                upper_left, bottom_right = r[0][0], r[0][2]
                x, y = (np.array(upper_left) + np.array(bottom_right)) / 2
                return x, y

    def get_similarity(self, texts, target, threshold=0.49):
        import difflib

        max_ratio = 0
        most_matched_name = ''
        for text in texts:
            if '_' in target:
                if target.strip('_') != text:
                    continue
            ratio = difflib.SequenceMatcher(None, text, target).quick_ratio()
            if ratio > max_ratio:
                max_ratio = ratio
                most_matched_name = text
        if max_ratio < threshold:
            return 0, ''
        return max_ratio, most_matched_name


OCR_MODEL = OcrModel()
