from functools import cached_property

import numpy as np

from module.ocr.nikke_ocr import NikkeOcr


class OcrModel:
    @cached_property
    def nikke(self):
        """
            base: cnocr-v2.3-densenet_lite_136-gru.ckpt
            training data: https://github.com/megumiss/NIKKECnOCR/commit/983d2f3542541163dbd695dd497e2961bfd41841
            epochs: 20
            learning_rate: 3e-4
            val-complete_match-epoch: 0.9731
        """
        return NikkeOcr(rec_model_name='densenet_lite_136-gru', root='./bin/cnocr_models/nikke',
                        model_name='/cnocr-v2.3-densenet_lite_136-gru-nikke.ckpt', name='nikke')

    @cached_property
    def cnocr(self):
        return NikkeOcr(rec_model_name='densenet_lite_136-gru', root='./bin/cnocr_models/cnocr',
                        model_name='/cnocr-v2.3-densenet_lite_136-gru.ckpt', name='cnocr')

    @cached_property
    def cnocr_num(self):
        
        
        return NikkeOcr(rec_model_name='number-densenet_lite_136-fc', root='./bin/cnocr_models/cnocr',
                        model_name='/cnocr-v2.3-number-densenet_lite_136-fc-nikke.ckpt', name='cnocr_num')

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
