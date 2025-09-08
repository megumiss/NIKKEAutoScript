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