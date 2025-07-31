from functools import cached_property

import numpy as np


class OcrModel:
    @cached_property
    def paddle(self):
        from module.ocr.nikke_ocr import NIKKEOcr

        return NIKKEOcr(
            lang='ch',
            ocr_version='PP-OCRv5',
            text_detection_model_name='PP-OCRv5_mobile_det',
            text_detection_model_dir='./bin/paddleocr/PP-OCRv5_mobile_det_infer',
            text_recognition_model_name='PP-OCRv5_mobile_rec',
            text_recognition_model_dir='./bin/paddleocr/PP-OCRv5_mobile_rec_infer',
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    @cached_property
    def paddle_num(self):
        from module.ocr.nikke_ocr import NIKKEOcr

        return NIKKEOcr(
            lang='en',
            ocr_version='PP-OCRv5',
            text_detection_model_name='PP-OCRv5_mobile_det',
            text_detection_model_dir='./bin/paddleocr/PP-OCRv5_mobile_det_infer',
            text_recognition_model_name='PP-OCRv5_mobile_rec',
            text_recognition_model_dir='./bin/paddleocr/PP-OCRv5_mobile_rec_infer',
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_det_thresh=0.1,
            text_det_unclip_ratio=5,
        )

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
