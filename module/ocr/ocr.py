import os
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

ColorRGB = Tuple[int, int, int]
TextColorInput = Union[
    ColorRGB,
    List[int],
    Dict[str, Sequence[int]],
]


class Ocr:
    SHOW_REVISE_WARNING = False
    # 预处理调试图保存目录；设为 None 则不落盘。
    DEBUG_SAVE_DIR = None
    # OCR 前放大倍率：增大可提升小字可读性，但过大可能导致笔画变粗。
    OCR_SCALE = 2
    # 文本预处理参数：
    # MASK_SOFTEN_SIGMA 颜色掩码柔化强度：用于补回抗锯齿边缘，值越大越容易连线也更容易糊。
    # MASK_EXPAND_THRESHOLD 掩码扩张阈值 (0~255)：值越小掩码越“厚”，值越大越“细”。
    # UPSCALE_BLUR_SIGMA 放大后灰度图的轻微模糊强度：用于提高白芯连续性，过大可能糊成块。
    TEXT_COLOR_PREPROCESS = (0.7, 20, 0.6)

    def __init__(
        self,
        buttons,
        lang='ch',
        model_type='mobile',
        interval=0,
        name=None,
        text_color: Optional[TextColorInput] = None,
        text_color_tolerance: Optional[Tuple[int, int, int]] = None,
        text_color_preprocess: Optional[Tuple[float, int, float]] = None,
    ):
        """
        Args:
            buttons (Button, tuple, list[Button], list[tuple]): OCR area.
            lang (str): 'ch' , 'en' or 'num'.
            model_type (str): 'mobile' or 'server'
            name (str):
            text_color (tuple/list/dict | None): 文字颜色（RGB）或 HSV 范围。
            text_color_tolerance (tuple | None): HSV 容差 (H, S, V)。
                不传则不启用 HSV 颜色筛选。
            text_color_preprocess (tuple | None): 文本预处理参数
                (mask_soften_sigma, mask_expand_threshold, upscale_blur_sigma)。
        """
        self.name = str(buttons) if isinstance(buttons, Button) else name
        self._buttons = buttons
        self.model_type = model_type
        self.lang = lang
        self.interval = interval
        self.text_color = text_color
        self.text_color_tolerance = text_color_tolerance
        self.text_color_preprocess = text_color_preprocess

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

    def _build_text_mask(
        self,
        image: np.ndarray,
        text_color: TextColorInput,
        tolerance: Optional[Tuple[int, int, int]] = None,
    ) -> Optional[np.ndarray]:
        if text_color is None:
            return None

        hsv_img = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        if isinstance(text_color, dict):
            lower = text_color.get('lower') or text_color.get('hsv_lower')
            upper = text_color.get('upper') or text_color.get('hsv_upper')
            hsv = text_color.get('hsv')
            tol = text_color.get('tolerance', tolerance)
            if lower is not None and upper is not None:
                lower = np.array(lower, dtype=np.uint8)
                upper = np.array(upper, dtype=np.uint8)
                mask = cv2.inRange(hsv_img, lower, upper)
            else:
                if hsv is None or tol is None:
                    return None
                hsv_color = np.array(hsv, dtype=np.uint8)
                ranges = self._hsv_ranges_from_color(hsv_color, tol)
                mask = None
                for h_range, s_range, v_range in ranges:
                    lower = np.array([h_range[0], s_range[0], v_range[0]], dtype=np.uint8)
                    upper = np.array([h_range[1], s_range[1], v_range[1]], dtype=np.uint8)
                    part = cv2.inRange(hsv_img, lower, upper)
                    mask = part if mask is None else cv2.bitwise_or(mask, part)
        else:
            if tolerance is None:
                return None
            rgb = np.array(text_color, dtype=np.uint8).reshape((1, 1, 3))
            hsv_color = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[0][0]
            ranges = self._hsv_ranges_from_color(hsv_color, tolerance)
            mask = None
            for h_range, s_range, v_range in ranges:
                lower = np.array([h_range[0], s_range[0], v_range[0]], dtype=np.uint8)
                upper = np.array([h_range[1], s_range[1], v_range[1]], dtype=np.uint8)
                part = cv2.inRange(hsv_img, lower, upper)
                mask = part if mask is None else cv2.bitwise_or(mask, part)

        return mask

    def _resolve_text_color_preprocess(
        self,
        text_color_preprocess: Optional[Tuple[float, int, float]] = None,
    ) -> Tuple[float, int, float]:
        values = self.text_color_preprocess if text_color_preprocess is None else text_color_preprocess
        if not isinstance(values, (tuple, list)) or len(values) != 3:
            values = self.TEXT_COLOR_PREPROCESS
        mask_soften_sigma = float(values[0])
        mask_expand_threshold = int(values[1])
        upscale_blur_sigma = float(values[2])
        return mask_soften_sigma, mask_expand_threshold, upscale_blur_sigma

    def pre_process(
        self,
        image,
        text_color: Optional[TextColorInput] = None,
        text_color_tolerance: Optional[Tuple[int, int, int]] = None,
        text_color_preprocess: Optional[Tuple[float, int, float]] = None,
    ):
        """
        Args:
            image (np.ndarray): Shape (height, width, channel)
            text_color (tuple/list/dict | None): 文字颜色（RGB）或 HSV 范围。
            text_color_tolerance (tuple | None): HSV 容差 (H, S, V)。
                不传则不启用 HSV 颜色筛选。
            text_color_preprocess (tuple | None): 文本预处理参数
                (mask_soften_sigma, mask_expand_threshold, upscale_blur_sigma)。

        Returns:
            np.ndarray: Shape (width, height)
        """
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        mask_soften_sigma, mask_expand_threshold, upscale_blur_sigma = self._resolve_text_color_preprocess(
            text_color_preprocess=text_color_preprocess
        )

        mask_applied = False
        if text_color is not None and len(image.shape) == 3:
            mask = self._build_text_mask(
                image,
                text_color,
                tolerance=text_color_tolerance,
            )
            if mask is not None and np.any(mask):
                # Mildly soften mask edges to recover anti-aliased strokes.
                softened = cv2.GaussianBlur(mask, (0, 0), sigmaX=mask_soften_sigma, sigmaY=mask_soften_sigma)
                _, expanded_mask = cv2.threshold(
                    softened,
                    mask_expand_threshold,
                    255,
                    cv2.THRESH_BINARY,
                )
                gray = cv2.bitwise_and(gray, gray, mask=expanded_mask)
                mask_applied = True

        # 先放大；仅在实际命中颜色掩码时再做轻微模糊。
        gray = self._scale_for_ocr(gray)
        if mask_applied and upscale_blur_sigma > 0:
            gray = cv2.GaussianBlur(gray, (0, 0), sigmaX=upscale_blur_sigma, sigmaY=upscale_blur_sigma)

        # Otsu二值化 -> 反色得到白底黑字
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        binary = cv2.bitwise_not(binary)
        # binary = self._clear_border_connected_black(binary)

        if self.DEBUG_SAVE_DIR:
            os.makedirs(self.DEBUG_SAVE_DIR, exist_ok=True)
            filename = f"ocr_pre_{int(time.time() * 1000)}.png"
            cv2.imwrite(os.path.join(self.DEBUG_SAVE_DIR, filename), binary)

        # 转回3通道
        binary_colored = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        return binary_colored.astype(np.uint8)

    def _scale_for_ocr(self, image: np.ndarray) -> np.ndarray:
        scale = self.OCR_SCALE
        if scale == 1 or scale is None:
            return image
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    def _clear_border_connected_black(self, binary: np.ndarray) -> np.ndarray:
        """
        Remove black connected-components that touch image borders.
        Input/Output is a binary grayscale image: black=0, white=255.
        """
        if binary.ndim != 2 or binary.size == 0:
            return binary

        black_mask = (binary == 0).astype(np.uint8)
        if not np.any(black_mask):
            return binary

        num_labels, labels = cv2.connectedComponents(black_mask, connectivity=8)
        if num_labels <= 1:
            return binary

        border_labels = np.unique(
            np.concatenate(
                [
                    labels[0, :],
                    labels[-1, :],
                    labels[:, 0],
                    labels[:, -1],
                ]
            )
        )
        border_labels = border_labels[border_labels != 0]
        if border_labels.size == 0:
            return binary

        cleaned = binary.copy()
        cleaned[np.isin(labels, border_labels)] = 255
        return cleaned

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
        text_color_tolerance: Optional[Tuple[int, int, int]] = None,
        text_color_preprocess: Optional[Tuple[float, int, float]] = None,
    ):
        """
        Args:
            image (np.ndarray, list[np.ndarray]):
            direct_ocr (bool): True to skip cropping.
            text_color (tuple/list/dict | None): 文字颜色（RGB）或 HSV 范围。
            text_color_tolerance (tuple | None): HSV 容差 (H, S, V)。
                不传则不启用 HSV 颜色筛选。
            text_color_preprocess (tuple | None): 文本预处理参数
                (mask_soften_sigma, mask_expand_threshold, upscale_blur_sigma)。

        Returns:
            list[str] or str
        """
        start_time = time.time()
        text_color = self.text_color if text_color is None else text_color
        text_color_tolerance = self.text_color_tolerance if text_color_tolerance is None else text_color_tolerance
        text_color_preprocess = self.text_color_preprocess if text_color_preprocess is None else text_color_preprocess

        # Otsu二值化处理
        images_to_ocr = []
        if direct_ocr:
            images_to_ocr = [image]
        else:
            images_to_ocr = [crop(image, area) for area in self.buttons]

        # Keep master behavior: only run heavy preprocess when text_color is explicitly provided.
        # This avoids over-binarizing generic text scenes (for example favorite_item_num).
        bbox_scale = 1.0
        if text_color is not None:
            images_to_ocr = [
                self.pre_process(
                    img,
                    text_color=text_color,
                    text_color_tolerance=text_color_tolerance,
                    text_color_preprocess=text_color_preprocess,
                )
                for img in images_to_ocr
            ]
            bbox_scale = float(self.OCR_SCALE or 1)

        result = self.paddleocr.predict(images_to_ocr)
        # 处理识别结果
        processed_result = self._process_ocr_result(result, threshold, bbox_scale=bbox_scale)
        processed_result['text'] = self.after_process(processed_result['text'])

        if show_log:
            logger.attr(
                name='%s %ss' % (self.name, float2str(time.time() - start_time)),
                text=str(processed_result['text'].replace('\n', ' ')),
            )

        return processed_result

    def _process_ocr_result(self, result: List[dict], threshold: float, bbox_scale: float = 1.0) -> Dict:
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

        scale = float(bbox_scale or 1)

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
                if scale != 1 and bbox is not None and np.size(bbox) > 0:
                    bbox = (np.array(bbox, dtype=np.float32) / scale).round().astype(int).tolist()

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
        self,
        buttons,
        lang='num',
        model_type='mobile',
        name=None,
        text_color: Optional[TextColorInput] = None,
        text_color_tolerance: Optional[Tuple[int, int, int]] = None,
        text_color_preprocess: Optional[Tuple[float, int, float]] = None,
    ):
        super().__init__(
            buttons,
            lang=lang,
            model_type=model_type,
            name=name,
            text_color=text_color,
            text_color_tolerance=text_color_tolerance,
            text_color_preprocess=text_color_preprocess,
        )

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
        self,
        buttons,
        lang='num',
        model_type='mobile',
        name=None,
        text_color: Optional[TextColorInput] = None,
        text_color_tolerance: Optional[Tuple[int, int, int]] = None,
        text_color_preprocess: Optional[Tuple[float, int, float]] = None,
    ):
        super().__init__(
            buttons,
            lang=lang,
            model_type=model_type,
            name=name,
            text_color=text_color,
            text_color_tolerance=text_color_tolerance,
            text_color_preprocess=text_color_preprocess,
        )

    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')
        return result

    def ocr(
        self,
        image,
        direct_ocr=False,
        text_color: Optional[TextColorInput] = None,
        text_color_tolerance: Optional[Tuple[int, int, int]] = None,
        text_color_preprocess: Optional[Tuple[float, int, float]] = None,
    ):
        """
        DigitCounter only support doing OCR on one button.
        Do OCR on a counter, such as `14/15`, and returns 14, 1, 15

        Returns:
            int, int, int: current, remain, total.
        """
        result_list = super().ocr(
            image,
            direct_ocr=direct_ocr,
            text_color=text_color,
            text_color_tolerance=text_color_tolerance,
            text_color_preprocess=text_color_preprocess,
        )
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
    def __init__(
        self,
        buttons,
        lang='en',
        model_type='mobile',
        name=None,
        text_color: Optional[TextColorInput] = None,
        text_color_tolerance: Optional[Tuple[int, int, int]] = None,
        text_color_preprocess: Optional[Tuple[float, int, float]] = None,
    ):
        super().__init__(
            buttons,
            lang=lang,
            model_type=model_type,
            name=name,
            text_color=text_color,
            text_color_tolerance=text_color_tolerance,
            text_color_preprocess=text_color_preprocess,
        )

    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')
        return result

    def ocr(
        self,
        image,
        direct_ocr=False,
        text_color: Optional[TextColorInput] = None,
        text_color_tolerance: Optional[Tuple[int, int, int]] = None,
        text_color_preprocess: Optional[Tuple[float, int, float]] = None,
    ):
        """
        Do OCR on a duration, such as `01:30:00`.

        Args:
            image:
            direct_ocr:

        Returns:
            list, datetime.timedelta: timedelta object, or a list of it.
        """
        result_list = super().ocr(
            image,
            direct_ocr=direct_ocr,
            text_color=text_color,
            text_color_tolerance=text_color_tolerance,
            text_color_preprocess=text_color_preprocess,
        )
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
