import re
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np  # 新增

from module.base.button import Button
from module.base.utils import crop, float2str
from module.logger import logger
from module.ocr.models import OCR_MODEL

if TYPE_CHECKING:
    from module.ocr.nikke_ocr import NIKKEOcr


from module.ocr.models import OCR_MODEL


TextColorInput = Union[
    Tuple[int, int, int],
    List[int],
    Dict[str, Sequence[int]],
]


class Ocr:
    SHOW_REVISE_WARNING = False
    HSV_TOLERANCE = (10, 80, 80)

    def __init__(
        self,
        buttons,
        lang='ch',
        model_type='mobile',
        interval=0,
        name=None,
        text_color: Optional[TextColorInput] = None,
    ):
        """
        Args:
            buttons (Button, tuple, list[Button], list[tuple]): OCR area.
            lang (str): 'ch' , 'en' or 'num'.
            model_type (str): 'mobile' or 'server'
            name (str):
            text_color (tuple/list/dict | None): 文字颜色（RGB）或 HSV 范围。
        """
        self.name = str(buttons) if isinstance(buttons, Button) else name
        self._buttons = buttons
        self.model_type = model_type
        self.lang = lang
        self.interval = interval
        self.text_color = text_color

    @property
    def paddleocr(self) -> 'NIKKEOcr':
        return OCR_MODEL.get_model_by(lang=self.lang, model_type=self.model_type, interval=self.interval)

    @property
    def buttons(self):
        buttons = self._buttons
        buttons = buttons if isinstance(buttons, list) else [buttons]
        buttons = [button.area if isinstance(button, Button) else button for button in buttons]
        return buttons

    @buttons.setter
    def buttons(self, value):
        self._buttons = value

    def _hsv_ranges_from_color(self, hsv_color: np.ndarray, tolerance: Tuple[int, int, int]) -> List[Tuple[int, int]]:
        h, s, v = [int(x) for x in hsv_color]
        dh, ds, dv = tolerance
        lower_s = max(0, s - ds)
        upper_s = min(255, s + ds)
        lower_v = max(0, v - dv)
        upper_v = min(255, v + dv)

        lower_h = h - dh
        upper_h = h + dh
        if 0 <= lower_h and upper_h <= 179:
            return [((lower_h, upper_h), (lower_s, upper_s), (lower_v, upper_v))]

        ranges = []
        if lower_h < 0:
            ranges.append(((0, upper_h), (lower_s, upper_s), (lower_v, upper_v)))
            ranges.append(((180 + lower_h, 179), (lower_s, upper_s), (lower_v, upper_v)))
        else:
            ranges.append(((0, upper_h - 180), (lower_s, upper_s), (lower_v, upper_v)))
            ranges.append(((lower_h, 179), (lower_s, upper_s), (lower_v, upper_v)))
        return ranges

    def _build_text_mask(self, image: np.ndarray, text_color: TextColorInput) -> Optional[np.ndarray]:
        if text_color is None:
            return None

        hsv_img = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        if isinstance(text_color, dict):
            lower = text_color.get('lower') or text_color.get('hsv_lower')
            upper = text_color.get('upper') or text_color.get('hsv_upper')
            hsv = text_color.get('hsv')
            tol = text_color.get('tolerance', self.HSV_TOLERANCE)
            if lower is not None and upper is not None:
                lower = np.array(lower, dtype=np.uint8)
                upper = np.array(upper, dtype=np.uint8)
                return cv2.inRange(hsv_img, lower, upper)
            if hsv is None:
                return None
            hsv_color = np.array(hsv, dtype=np.uint8)
            ranges = self._hsv_ranges_from_color(hsv_color, tol)
        else:
            rgb = np.array(text_color, dtype=np.uint8).reshape((1, 1, 3))
            hsv_color = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[0][0]
            ranges = self._hsv_ranges_from_color(hsv_color, self.HSV_TOLERANCE)

        mask = None
        for h_range, s_range, v_range in ranges:
            lower = np.array([h_range[0], s_range[0], v_range[0]], dtype=np.uint8)
            upper = np.array([h_range[1], s_range[1], v_range[1]], dtype=np.uint8)
            part = cv2.inRange(hsv_img, lower, upper)
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        return mask

    def pre_process(self, image, text_color: Optional[TextColorInput] = None):
        """
        Args:
            image (np.ndarray): Shape (height, width, channel)
            text_color (tuple/list/dict | None): 文字颜色（RGB）或 HSV 范围。

        Returns:
            np.ndarray: Shape (width, height)
        """
        if text_color is not None and len(image.shape) == 3:
            mask = self._build_text_mask(image, text_color)
            if mask is not None and np.any(mask):
                image = cv2.bitwise_and(image, image, mask=mask)

        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        # Otsu二值化
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 转回3通道
        binary_colored = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        return binary_colored.astype(np.uint8)

    def after_process(self, result):
        """
        Args:
            result (str): OCR result string

        Returns:
            str:
        """
        return result

    def ocr(
        self,
        image,
        direct_ocr=False,
        threshold: float = 0.51,
        show_log=True,
        text_color: Optional[TextColorInput] = None,
    ):
        """
        Args:
            image (np.ndarray, list[np.ndarray]):
            direct_ocr (bool): True to skip cropping.
            text_color (tuple/list/dict | None): 文字颜色（RGB）或 HSV 范围。

        Returns:
            list[str] or str
        """
        start_time = time.time()
        text_color = self.text_color if text_color is None else text_color

        # Otsu二值化处理
        images_to_ocr = []
        if direct_ocr:
            images_to_ocr = [image]
        else:
            images_to_ocr = [crop(image, area) for area in self.buttons]

        if text_color is not None:
            images_to_ocr = [self.pre_process(img, text_color=text_color) for img in images_to_ocr]

        result = self.paddleocr.predict(images_to_ocr)
        # 处理识别结果
        processed_result = self._process_ocr_result(result, threshold)
        processed_result['text'] = self.after_process(processed_result['text'])

        if show_log:
            logger.attr(
                name='%s %ss' % (self.name, float2str(time.time() - start_time)),
                text=str(processed_result['text'].replace('\n', ' ')),
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
                'text': str,               # 合并后的文本
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

        combined_text = ''.join(text_lines)
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
    Method ocr() returns digit string, or a list of digit strings.
    """

    def __init__(
        self, buttons, lang='num', model_type='mobile', name=None, text_color: Optional[TextColorInput] = None
    ):
        super().__init__(buttons, lang=lang, model_type=model_type, name=name, text_color=text_color)

    def after_process(self, result):
        result = super().after_process(result)
        prev = result

        # OCR 容易混淆的字符映射
        replacements = {
            'I': '1',
            'D': '0',
            'S': '5',
            'B': '8',
            'G': '6',
            'O': '0',
            'Q': '0',
            '|': '1',
        }

        # 只取 '/' 前的部分
        before_slash = result.split('/', 1)[0]

        # 先找纯数字
        m = re.search(r'\d+', before_slash)
        if m:
            final = m.group(0)
        else:
            # 检查是否全由 数字+易混字符 组成
            ambiguous_set = r'0-9IDSBGOQ\|'  # 定义数字样式字符
            # 全字匹配
            if re.fullmatch(rf'[{ambiguous_set}]+', before_slash):
                candidate = before_slash
                for k, v in replacements.items():
                    candidate = candidate.replace(k, v)
                m2 = re.search(r'\d+', candidate)
                final = m2.group(0) if m2 else '0'
            else:
                # 包含普通字母 => 不做替换，返回 0
                final = '0'

        if self.SHOW_REVISE_WARNING and final != prev:
            logger.warning(f'OCR {self.name}: Result "{prev}" is revised to "{final}"')

        return final


class DigitCounter(Ocr):
    def __init__(
        self, buttons, lang='num', model_type='mobile', name=None, text_color: Optional[TextColorInput] = None
    ):
        super().__init__(buttons, lang=lang, model_type=model_type, name=name, text_color=text_color)

    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')
        return result

    def ocr(self, image, direct_ocr=False, text_color: Optional[TextColorInput] = None):
        """
        DigitCounter only support doing OCR on one button.
        Do OCR on a counter, such as `14/15`, and returns 14, 1, 15

        Returns:
            int, int, int: current, remain, total.
        """
        result_list = super().ocr(image, direct_ocr=direct_ocr, text_color=text_color)
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
    def __init__(self, buttons, lang='en', model_type='mobile', name=None, text_color: Optional[TextColorInput] = None):
        super().__init__(buttons, lang=lang, model_type=model_type, name=name, text_color=text_color)

    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')
        return result

    def ocr(self, image, direct_ocr=False, text_color: Optional[TextColorInput] = None):
        """
        Do OCR on a duration, such as `01:30:00`.

        Args:
            image:
            direct_ocr:

        Returns:
            list, datetime.timedelta: timedelta object, or a list of it.
        """
        result_list = super().ocr(image, direct_ocr=direct_ocr, text_color=text_color)
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
