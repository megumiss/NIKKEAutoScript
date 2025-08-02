import re
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Dict, List

from module.base.button import Button
from module.base.utils import crop, float2str
from module.logger import logger
from module.ocr.models import OCR_MODEL

if TYPE_CHECKING:
    from module.ocr.nikke_ocr import NIKKEOcr

from module.ocr.models import OCR_MODEL


class Ocr:
    SHOW_LOG = True
    SHOW_REVISE_WARNING = False

    def __init__(self, buttons, lang='ch', model_type='mobile', name=None):
        """
        Args:
            buttons (Button, tuple, list[Button], list[tuple]): OCR area.
            lang (str): 'ch' , 'en' or 'num'.
            model_type (str): 'mobile' or 'server'
            name (str):
        """
        self.name = str(buttons) if isinstance(buttons, Button) else name
        self._buttons = buttons
        self.model_type = model_type
        self.lang = lang

    @property
    def paddleocr(self) -> 'NIKKEOcr':
        return OCR_MODEL.get_model_by(lang=self.lang, model_type=self.model_type)

    @property
    def buttons(self):
        buttons = self._buttons
        buttons = buttons if isinstance(buttons, list) else [buttons]
        buttons = [button.area if isinstance(button, Button) else button for button in buttons]
        return buttons

    @buttons.setter
    def buttons(self, value):
        self._buttons = value

    def after_process(self, result):
        """
        Args:
            result (str): OCR result string

        Returns:
            str:
        """
        return result

    def ocr(self, image, direct_ocr=False, threshold: float = 0.51):
        """
        Args:
            image (np.ndarray, list[np.ndarray]):
            direct_ocr (bool): True to skip cropping.

        Returns:
            list[str] or str
        """
        start_time = time.time()

        if direct_ocr:
            image_list = image if isinstance(image, list) else [image]
        else:
            image_list = [crop(image, area) for area in self.buttons]

        result = self.paddleocr.predict(image_list)
        # 处理识别结果
        processed_result = self._process_ocr_result(result, threshold)

        if self.SHOW_LOG:
            logger.attr(
                name='%s %ss' % (self.name, float2str(time.time() - start_time)), text=str(processed_result['text'])
            )

        return processed_result

    def _process_ocr_result(self, result: List[dict], threshold: float) -> Dict:
        """
        处理 Paddlex OCR dict 格式的识别结果，仅使用 rec_texts/rec_scores/rec_boxes。

        Args:
            result: OCR 原始结果，每项为 dict，需包含 'rec_texts', 'rec_scores', 'rec_boxes'
            threshold: 置信度阈值

        Returns:
            Dict: {
                'text': str,               # 合并后的文本（按行 \n 分隔）
                'details': List[dict],     # 每行的详细信息
                'stats': {
                    'total_lines': int,    # 有效行数
                    'total_chars': int,    # 总字符数（不含空格和换行）
                    'avg_confidence': float,# 平均置信度
                    'confidence_threshold': float,
                }
            }
        """
        text_lines = []
        details = []
        total_conf = 0.0
        valid_lines = 0

        if not result:
            return {
                'text': '',
                'details': [],
                'stats': {
                    'total_lines': 0,
                    'total_chars': 0,
                    'avg_confidence': 0.0,
                    'confidence_threshold': threshold,
                },
            }

        for page in result:
            rec_texts = page.get('rec_texts', [])
            rec_scores = page.get('rec_scores', [])
            rec_boxes = page.get('rec_boxes', [])

            # 按文本顺序处理
            for idx, (txt, score) in enumerate(zip(rec_texts, rec_scores)):
                text = txt.strip()
                confidence = float(score)
                if confidence < threshold or not text:
                    continue

                bbox = rec_boxes[idx] if idx < len(rec_boxes) else []

                valid_lines += 1
                total_conf += confidence
                text_lines.append(text)
                details.append(
                    {
                        'line_number': valid_lines,
                        'text': text,
                        'confidence': confidence,
                        'bbox': bbox,
                        'char_count': len(text),
                    }
                )

        combined_text = '\n'.join(text_lines)
        avg_conf = (total_conf / valid_lines) if valid_lines > 0 else 0.0
        total_chars = len(combined_text.replace('\n', '').replace(' ', ''))

        return {
            'text': combined_text,
            'details': details,
            'stats': {
                'total_lines': valid_lines,
                'total_chars': total_chars,
                'avg_confidence': avg_conf,
                'confidence_threshold': threshold,
            },
        }


class Digit(Ocr):
    """
    Do OCR on a digit, such as `45`.
    Method ocr() returns int, or a list of int.
    """

    def __init__(self, buttons, lang='num', model_type='mobile', name=None):
        super().__init__(buttons, lang=lang, model_type=model_type, name=name)

    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')

        prev = result
        result = int(result) if result else 0
        if self.SHOW_REVISE_WARNING:
            if str(result) != prev:
                logger.warning(f'OCR {self.name}: Result "{prev}" is revised to "{result}"')

        return result


class DigitCounter(Ocr):
    def __init__(self, buttons, lang='num', model_type='mobile', name=None):
        super().__init__(buttons, lang=lang, model_type=model_type, name=name)

    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')
        return result

    def ocr(self, image, direct_ocr=False):
        """
        DigitCounter only support doing OCR on one button.
        Do OCR on a counter, such as `14/15`, and returns 14, 1, 15

        Returns:
            int, int, int: current, remain, total.
        """
        result_list = super().ocr(image, direct_ocr=direct_ocr)
        result = result_list[0] if isinstance(result_list, list) else result_list

        result = re.search(r'(\d+)/(\d+)', result)
        if result:
            current, total = map(int, result.groups())
            current = min(current, total)
            return current, total - current, total
        else:
            logger.warning(f'Unexpected ocr result: {result_list}')
            return 0, 0, 0


class Duration(Ocr):
    def __init__(self, buttons, lang='en', model_type='mobile', name=None):
        super().__init__(buttons, lang=lang, model_type=model_type, name=name)

    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')
        return result

    def ocr(self, image, direct_ocr=False):
        """
        Do OCR on a duration, such as `01:30:00`.

        Args:
            image:
            direct_ocr:

        Returns:
            list, datetime.timedelta: timedelta object, or a list of it.
        """
        result_list = super().ocr(image, direct_ocr=direct_ocr)
        if not isinstance(result_list, list):
            result_list = [result_list]
        result_list = [self.parse_time(result) for result in result_list]
        if len(self.buttons) == 1:
            result_list = result_list[0]
        return result_list

    @staticmethod
    def parse_time(string):
        """
        Args:
            string (str): `01:30:00`

        Returns:
            datetime.timedelta:
        """
        result = re.search(r'(\d{1,2}):?(\d{2}):?(\d{2})', string)
        if result:
            result = [int(s) for s in result.groups()]
            return timedelta(hours=result[0], minutes=result[1], seconds=result[2])
        else:
            logger.warning(f'Invalid duration: {string}')
            return timedelta(hours=0, minutes=0, seconds=0)
