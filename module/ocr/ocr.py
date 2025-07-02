import re
import time
from typing import TYPE_CHECKING

import numpy as np

from module.base.button import Button
from module.base.utils import crop, extract_letters, float2str
from module.logger import logger
from module.ocr.models import OCR_MODEL

if TYPE_CHECKING:
    from module.ocr.nikke_ocr import NikkeOcr


class Ocr:
    SHOW_LOG = True

    def __init__(
        self,
        buttons,
        lang='nikke',
        letter=(255, 255, 255),
        threshold=128,
        alphabet=None,
        name=None,
    ):
        """
        Args:
            buttons (Button, tuple, list[Button], list[tuple]): OCR area.
            lang (str): 'azur_lane' or 'cnocr'.
            letter (tuple(int)): Letter RGB.
            threshold (int):
            alphabet: Alphabet white list.
            name (str):
        """
        self.name = str(buttons) if isinstance(buttons, Button) else name
        self._buttons = buttons
        self.letter = letter
        self.threshold = threshold
        self.alphabet = alphabet
        self.lang = lang

    @property
    def cnocr(self) -> 'NikkeOcr':
        return OCR_MODEL.__getattribute__(self.lang)

    @property
    def buttons(self):
        buttons = self._buttons
        buttons = buttons if isinstance(buttons, list) else [buttons]
        buttons = [button.area if isinstance(button, Button) else button for button in buttons]
        return buttons

    @buttons.setter
    def buttons(self, value):
        self._buttons = value

    def pre_process(self, image):
        """
        Args:
            image (np.ndarray): Shape (height, width, channel)

        Returns:
            np.ndarray: Shape (width, height)
        """
        image = extract_letters(image, letter=self.letter, threshold=self.threshold)

        return image.astype(np.uint8)

    def after_process(self, result):
        """
        Args:
            result (str): '第二行'

        Returns:
            str:
        """
        return result

    def ocr(self, image, direct_ocr=False):
        """
        Args:
            image (np.ndarray, list[np.ndarray]):
            direct_ocr (bool): True to skip preprocess.

        Returns:

        """
        start_time = time.time()

        if direct_ocr:
            # image_list = [self.pre_process() for i in image]
            image_list = [i for i in image]
        else:
            # image_list = [self.pre_process(crop(image, area)) for area in self.buttons]
            image_list = [crop(image, area) for area in self.buttons]

        result_list = self.cnocr.ocr_for_single_lines(image_list)
        result_list = [''.join(result.get('text', None)) for result in result_list]
        result_list = [self.after_process(result) for result in result_list]

        if len(self.buttons) == 1:
            result_list = result_list[0]
        if Ocr.SHOW_LOG:
            logger.attr(
                name='%s %ss' % (self.name, float2str(time.time() - start_time)),
                text=str(result_list),
            )

        return result_list


class Digit(Ocr):
    """
    Do OCR on a digit, such as `45`.
    Method ocr() returns int, or a list of int.
    """

    def __init__(
        self,
        buttons,
        lang='nikke',
        letter=(255, 255, 255),
        threshold=128,
        alphabet='0123456789IDSBO',
        name=None,
    ):
        super().__init__(
            buttons,
            lang=lang,
            letter=letter,
            threshold=threshold,
            alphabet=alphabet,
            name=name,
        )

    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace('I', '1').replace('D', '0').replace('S', '5')
        result = result.replace('B', '8')
        result = result.replace('O', '0')

        prev = result
        result = int(result) if result else 0
        if str(result) != prev:
            logger.warning(f'OCR {self.name}: Result "{prev}" is revised to "{result}"')

        return result


class DigitCounter(Ocr):
    def __init__(
        self,
        buttons,
        lang='nikke',
        letter=(255, 255, 255),
        threshold=128,
        alphabet='0123456789/IDS',
        name=None,
    ):
        super().__init__(
            buttons,
            lang=lang,
            letter=letter,
            threshold=threshold,
            alphabet=alphabet,
            name=name,
        )

    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace('I', '1').replace('D', '0').replace('S', '5')
        return result

    def ocr(self, image, direct_ocr=False):
        """
        DigitCounter only support doing OCR on one button.
        Do OCR on a counter, such as `14/15`, and returns 14, 1, 15

        Args:
            image:
            direct_ocr:

        Returns:
            int, int, int: current, remain, total.
        """
        result_list = super().ocr(image, direct_ocr=direct_ocr)
        result = result_list[0] if isinstance(result_list, list) else result_list

        result = re.search(r'(\d+)/(\d+)', result)
        if result:
            result = [int(s) for s in result.groups()]
            current, total = int(result[0]), int(result[1])
            current = min(current, total)
            return current, total - current, total
        else:
            logger.warning(f'Unexpected ocr result: {result_list}')
            return 0, 0, 0


import os
import cv2

if __name__ == '__main__':
    os.chdir(os.path.join(os.path.dirname(__file__), '../../'))

    # ocr = CnOcr(rec_model_name='densenet_lite_136-gru', rec_model_fp=f'./bin/cnocr_models/nikke/1111.ckpt',
    #             rec_model_backend='pytorch')
    #
    name = 'text290'
    # img = cv2.imread(f'./pic/{name}.png')

    ocr = OCR_MODEL.nikke.ocr
    # img = cv2.imread('./pic/t2b.png')
    img = cv2.imread('./pic/55566_c.png')
    # img = cv2.imread('./pic/text393_2.png')
    print(ocr(img))

    # print(ocr.ocr_for_single_line(img))
    # cnocr = CnOcr()
    # ocr = OCR_MODEL.nikke.ocr

    # cv2.imshow('a', crop(img, (165, 255, 560, 290)))
    # cv2.waitKey(0)
    # print(ocr(img))
    # print(ocr(img))

    # nkas = NikkeAutoScript()
    # img = nkas.device.screenshot()

    # img = crop(img, (208, 300, 268, 336))
    # area = [(446, 660, 505, 692), (446, 790, 510, 830), (450, 930, 510, 960)]

    # area = [(274, 420, 320, 450)]
    # a = [crop(img, i) for i in area]
    # result = [ocr(i) for i in a]
    # print(result)
    # result = OCR_MODEL.nikke.ocr_for_single_lines(a)
    # for i, image in enumerate(a):
    #     cv2.imwrite(f'./t{i}.png', image)
    # cv2.imshow('a', i)
    # cv2.waitKey(0)

    # cv2.imwrite('./tt.png', img)

    # cv2.imshow('a', image)
    # cv2.waitKey(0)
    # for i in image:
    #     print(i)

    # black_pixels = np.where(img < 128)
    # min_row, min_col = np.min(black_pixels, axis=1)
    # max_row, max_col = np.max(black_pixels, axis=1)

    # 计算黑色文字的矩形位置
    # text_rect = (min_col, min_row, max_col, max_row)
    # print(text_rect)
    # img = crop(img, text_rect)
    # cv2.imshow('a', img)
    # cv2.waitKey(0)
    # location = OCR_MODEL.get_location('_领取奖励', ocr(img))
    # print(location)

    # img_fp = crop(img, (230, 430, 700, 550))
    # cv2.imshow('a', img_fp)
    # cv2.waitKey(0)
    # img = img[430:550, 230:700]

    # print(ocr(img))

    # print(cnocr.ocr(img))
    # ocr = CnOcr(rec_model_name='densenet_lite_136-gru')
    # res = ocr.ocr(img, resized_shape=(2000, 2000))
    # res = ocr.ocr(img)
    # print(res)
    # res = ocr.filter(res)
    # location = ocr.get_location(text, res)
    # print(location)
