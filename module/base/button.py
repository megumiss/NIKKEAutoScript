import copy
import os
from functools import cached_property

import cv2
import imageio
import numpy as np

from module.logger import logger
from module.base.resource import Resource
from module.base.utils import *


class Button(Resource):
    def __init__(self, area, color, button, file=None, name=None):
        """Initialize a Button instance.

        Args:
            area (dict[tuple], tuple): Area that the button would appear on the image.
                          (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
            color (dict[tuple], tuple): Color we expect the area would be.
                           (r, g, b)
            button (dict[tuple], tuple): Area to be click if button appears on the image.
                            (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
                            If tuple is empty, this object can be use as a checker.
        Examples:
            BATTLE_PREPARATION = Button(
                area=(1562, 908, 1864, 1003),
                color=(231, 181, 90),
                button=(1562, 908, 1864, 1003)
            )
        """
        self.raw_area = area
        self.raw_color = color
        self.raw_button = button
        self.raw_file = file
        self.raw_name = name

        # 非模板位置，例如'确认'并不是固定的
        self._button_offset = None
        self._match_init = False
        self._match_binary_init = False
        self._match_luma_init = False
        self.image = None
        self.image_binary = None
        self.image_luma = None

        if self.file:
            self.resource_add(key=self.file)

    @cached_property
    def name(self):
        if self.raw_name:
            return self.raw_name
        elif self.file:
            return os.path.splitext(os.path.split(self.file)[1])[0]
        else:
            return 'BUTTON'

    @cached_property
    def file(self):
        return self.parse_property(self.raw_file)

    @cached_property
    def area(self):
        return self.parse_property(self.raw_area)

    @cached_property
    def color(self):
        return self.parse_property(self.raw_color)

    @cached_property
    def _button(self):
        return self.parse_property(self.raw_button)

    @property
    def button(self):
        if self._button_offset is None:
            return self._button
        else:
            return self._button_offset

    @property
    def location(self):
        return find_center(self.button)

    @cached_property
    def is_gif(self):
        if self.file:
            return os.path.splitext(self.file)[1] == '.gif'
        else:
            return False

    def __str__(self):
        return self.name

    def ensure_template(self):
        """
        Load asset image.
        If needs to call self.match, call this first.
        """
        if not self._match_init:
            if self.is_gif:
                self.image = []
                for image in imageio.mimread(self.file):
                    image = image[:, :, :3].copy() if len(image.shape) == 3 else image
                    image = crop(image, self.area)
                    self.image.append(image)
            else:
                self.image = load_image(self.file, self.area)
            self._match_init = True

    def ensure_luma_template(self):
        if not self._match_luma_init:
            if self.is_gif:
                self.image_luma = []
                for image in self.image:
                    luma = rgb2luma(image)
                    self.image_luma.append(luma)
            else:
                self.image_luma = rgb2luma(self.image)
            self._match_luma_init = True

    def match(self, image, offset=30, threshold=0.85, static=True) -> bool:
        self.ensure_template()
        if static:
            if isinstance(offset, tuple):
                if len(offset) == 2:
                    offset = np.array((-offset[0], -offset[1], offset[0], offset[1]))
                else:
                    offset = np.array(offset)
            else:
                offset = np.array((-3, -offset, 3, offset))

            image = crop(image, offset + self.area)

        res = cv2.matchTemplate(self.image, image, cv2.TM_CCOEFF_NORMED)
        _, similarity, _, upper_left = cv2.minMaxLoc(res)

        if similarity > threshold:
            if static:
                self._button_offset = area_offset(self._button, offset[:2] + np.array(upper_left))
            else:
                h, w = self.area[3] - self.area[1], self.area[2] - self.area[0]
                bottom_right = (upper_left[0] + w, upper_left[1] + h)
                self._button_offset = (upper_left[0], upper_left[1], bottom_right[0], bottom_right[1])

        logger.debug(f'Button: {self.name}, similarity: {similarity}, threshold: {threshold}, hit: {similarity > threshold}')
        return similarity > threshold

        # if self.is_gif:
        #     for template in self.image:
        #         res = cv2.matchTemplate(template, image, cv2.TM_CCOEFF_NORMED)
        #         _, similarity, _, point = cv2.minMaxLoc(res)
        #         self._button_offset = area_offset(self._button, offset[:2] + np.array(point))
        #         if similarity > threshold:
        #             return True
        #     return False
        # else:

    def match_with_scale(self, image, threshold=0.85, scale_range=(0.9, 1.1), scale_step=0.02):
        """
        多尺度匹配：在一定范围内连续搜索最佳缩放匹配

        Args:
            image: 要匹配的图像
            offset (int | tuple): 匹配区域偏移
            threshold (float): 相似度阈值
            scale_range (tuple[float, float]): 缩放范围 (min_scale, max_scale)
            scale_step (float): 缩放步长

        Returns:
            bool: 是否匹配成功
        """
        self.ensure_template()

        best_similarity = -1
        best_scale = 1.0
        best_loc = None

        # 在范围内连续扫描比例
        scale = scale_range[0]
        while scale <= scale_range[1]:
            resized_template = cv2.resize(self.image, (0, 0), fx=scale, fy=scale)
            if resized_template.shape[0] > image.shape[0] or resized_template.shape[1] > image.shape[1]:
                scale += scale_step
                continue

            res = cv2.matchTemplate(image, resized_template, cv2.TM_CCOEFF_NORMED)
            _, similarity, _, upper_left = cv2.minMaxLoc(res)

            if similarity > best_similarity:
                best_similarity = similarity
                best_scale = scale
                best_loc = upper_left

            scale += scale_step

        if best_similarity > threshold:
            h, w = self.area[3] - self.area[1], self.area[2] - self.area[0]
            bottom_right = (best_loc[0] + w, best_loc[1] + h)
            self._button_offset = (best_loc[0], best_loc[1], bottom_right[0], bottom_right[1])

        logger.debug(
            f'Button: {self.name}, best_similarity: {best_similarity:.3f}, '
            f'best_scale: {best_scale:.3f}, threshold: {threshold}, hit: {best_similarity > threshold}'
        )
        return best_similarity > threshold

    def match_luma(self, image, offset=30, similarity=0.85):
        """
        Detects button by template matching under Y channel (Luminance)

        Args:
            image: Screenshot.
            offset (int, tuple): Detection area offset.
            similarity (float): 0-1. Similarity.

        Returns:
            bool.
        """
        self.ensure_template()
        self.ensure_luma_template()

        if isinstance(offset, tuple):
            if len(offset) == 2:
                offset = np.array((-offset[0], -offset[1], offset[0], offset[1]))
            else:
                offset = np.array(offset)
        else:
            offset = np.array((-3, -offset, 3, offset))
        image = crop(image, offset + self.area)

        if self.is_gif:
            image_luma = rgb2luma(image)
            for template in self.image_luma:
                res = cv2.matchTemplate(template, image_luma, cv2.TM_CCOEFF_NORMED)
                _, sim, _, point = cv2.minMaxLoc(res)
                self._button_offset = area_offset(self._button, offset[:2] + np.array(point))
                if sim > similarity:
                    return True
        else:
            image_luma = rgb2luma(image)
            res = cv2.matchTemplate(self.image_luma, image_luma, cv2.TM_CCOEFF_NORMED)
            _, sim, _, point = cv2.minMaxLoc(res)
            self._button_offset = area_offset(self._button, offset[:2] + np.array(point))
            print(f'luma similarity: {sim}')
            return sim > similarity

    def match_several(self, image, offset=30, threshold=0.85, static=True) -> list[dict]:
        # areas = set()
        areas = []
        while 1:
            if self.match(image, offset=offset, threshold=threshold, static=static):
                areas.append({'area': self._button_offset, 'location': self.location})
                image = mask_area(image, self._button_offset)
            else:
                return areas

    def appear_on(self, image, threshold=10) -> bool:
        """Check if the button appears on the image.

        Args:
            image (np.ndarray): Screenshot.
            threshold (int): Default to 10.

        Returns:
            bool: True if button appears on screenshot.
        """
        color1 = get_color(image, self.area)
        similar = color_similar(
            color1=color1,
            color2=self.color
        )

        logger.debug(f'Button: {self.name}, color1: {color1}, color2: {self.color}, similarity: {similar}, threshold: {threshold}, hit: {similar <= threshold}')
        return similar <= threshold

    def match_appear_on(self, image, threshold=30) -> bool:
        """
        Args:
            image: Screenshot.
            threshold: Default to 10.

        Returns:
            bool:
        """
        diff = np.subtract(self.button, self._button)[:2]
        area = area_offset(self.area, offset=diff)

        color1 = get_color(image, area)
        similar = color_similar(
            color1=color1,
            color2=self.color
        )

        logger.debug(f'Button: {self.name}, color1: {color1}, color2: {self.color}, similarity: {diff}, threshold: {threshold}, hit: {diff <= threshold}')
        return similar <= threshold

    def load_color(self, image):
        """Load color from the specific area of the given image.
        This method is irreversible, this would be only use in some special occasion.

        Args:
            image: Another screenshot.

        Returns:
            tuple: Color (r, g, b).
        """
        self.__dict__['color'] = get_color(image, self.area)
        self.image = crop(image, self.area)
        self.__dict__['is_gif'] = False
        return self.color

    def load_offset(self, button):
        """
        Load offset from another button.

        Args:
            button (Button):
        """
        offset = np.subtract(button.button, button._button)[:2]
        self._button_offset = area_offset(self._button, offset=offset)
        
    def crop(self, area, image=None, name=None):
        """
        Get a new button by relative coordinates.

        Args:
            area (tuple):
            image (np.ndarray): Screenshot. If provided, load color and image from it.
            name (str):

        Returns:
            Button:
        """
        if name is None:
            name = self.name
        new_area = area_offset(area, offset=self.area[:2])
        new_button = area_offset(area, offset=self.button[:2])
        button = Button(area=new_area, color=self.color, button=new_button, file=self.file, name=name)
        if image is not None:
            button.load_color(image)
        return button

    def move(self, vector, image=None, name=None):
        """
        Move button.

        Args:
            vector (tuple):
            image (np.ndarray): Screenshot. If provided, load color and image from it.
            name (str):

        Returns:
            Button:
        """
        if name is None:
            name = self.name
        new_area = area_offset(self.area, offset=vector)
        new_button = area_offset(self.button, offset=vector)
        button = Button(area=new_area, color=self.color, button=new_button, file=self.file, name=name)
        if image is not None:
            button.load_color(image)
        return button

def filter_buttons_in_area(
    buttons: list[Button], x_range: tuple[int, int] = None, y_range: tuple[int, int] = None
) -> list[Button]:
    """
    筛选在指定范围内的 Button
    - 可以只指定 x_range 或 y_range
    - 按 Button 的 area 来判断
    """
    filtered = []
    for btn in buttons:
        x1, y1, x2, y2 = btn.area
        if x_range:
            if x1 < x_range[0] or x2 > x_range[1]:
                continue
        if y_range:
            if y1 < y_range[0] or y2 > y_range[1]:
                continue
        filtered.append(btn)
    return filtered

def merge_buttons(
    buttons: list[Button], x_threshold: int = 10, y_threshold: int = 10
) -> list[Button]:
    """
    根据阈值合并接近的 Button
    - 如果两个按钮区域在阈值范围内接近，只保留一个（默认保留先出现的）
    - x_threshold: 横向阈值
    - y_threshold: 纵向阈值
    """
    merged = []

    for btn in buttons:
        x1, y1, x2, y2 = btn.area
        is_duplicate = False

        for m in merged:
            mx1, my1, mx2, my2 = m.area

            # 判断是否在阈值范围内
            if (
                abs(x1 - mx1) <= x_threshold and
                abs(y1 - my1) <= y_threshold and
                abs(x2 - mx2) <= x_threshold and
                abs(y2 - my2) <= y_threshold
            ):
                is_duplicate = True
                break

        if not is_duplicate:
            merged.append(btn)

    return merged

def shift_button(button: Button, dx: int = 0, dy: int = 0) -> Button:
    """
    复制并平移一个已经初始化的 Button 实例。
    """
    # 获取当前按钮的实际坐标
    x1, y1, x2, y2 = button.area
    bx1, by1, bx2, by2 = button.button

    # 偏移
    new_area = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
    new_button = (bx1 + dx, by1 + dy, bx2 + dx, by2 + dy)

    # 重新创建一个 Button 对象（带偏移）
    shifted = Button(
        area=new_area,
        color=button.color,
        button=new_button,
        file=button.file,
        name=f"{button.name}_SHIFTED"
    )

    return shifted
