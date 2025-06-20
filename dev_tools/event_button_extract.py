import os

import imageio
import numpy as np
from tqdm.contrib.concurrent import process_map

from module.base.utils import load_image, get_bbox, get_color, image_size
from module.config.config import NikkeConfig
from module.logger import logger

MODULE_FOLDER = './module'
BUTTON_FILE = 'assets.py'
IMPORT_EXP = """
from module.base.button import Button
from module.base.template import Template

# This file was automatically generated by dev_tools/event_button_extract.py.
# Don't modify it manually.
"""
IMPORT_EXP = IMPORT_EXP.strip().split('\n') + ['']


class ImageExtractor:
    def __init__(self, module, file):
        """
        Args:
            module(str):
            file(str): xxx.png or xxx.gif
        """
        self.module = module
        self.name, self.ext = os.path.splitext(file)
        self.area, self.color, self.button, self.file = {}, {}, {}, {}
        # for server in VALID_SERVER:
        #     self.load(server)
        self.load()

    def get_file(self, genre='', server='cn'):
        for ext in ['.png', '.gif']:
            file = f'{self.name}.{genre}{ext}' if genre else f'{self.name}{ext}'
            # TODO
            # file = os.path.join(NikkeConfig.ASSETS_FOLDER, server, self.module, file).replace('\\', '/')
            file = os.path.join(NikkeConfig.ASSETS_FOLDER, 'story_event', self.module, file).replace('\\', '/')
            if os.path.exists(file):
                return file

        ext = '.png'
        file = f'{self.name}.{genre}{ext}' if genre else f'{self.name}{ext}'
        file = os.path.join(NikkeConfig.ASSETS_FOLDER, 'story_event', self.module, file).replace('\\', '/')
        return file

    def extract(self, file):
        if os.path.splitext(file)[1] == '.gif':
            # In a gif Button, use the first image.
            bbox = None
            mean = None
            for image in imageio.mimread(file):
                image = image[:, :, :3] if len(image.shape) == 3 else image
                new_bbox, new_mean = self._extract(image, file)
                if bbox is None:
                    bbox = new_bbox
                elif bbox != new_bbox:
                    logger.warning(f'{file} has multiple different bbox, this will cause unexpected behaviour')
                if mean is None:
                    mean = new_mean
            return bbox, mean
        else:
            image = load_image(file)
            return self._extract(image, file)

    @staticmethod
    def _extract(image, file):
        size = image_size(image)
        if size != (720, 1280):
            logger.warning(f'{file} has wrong resolution: {size}')
        bbox = get_bbox(image)
        mean = get_color(image=image, area=bbox)
        mean = tuple(np.rint(mean).astype(int))
        return bbox, mean

    def load(self, server='cn'):
        file = self.get_file(server=server)
        if os.path.exists(file):
            area, color = self.extract(file)
            button = area
            override = self.get_file('AREA', server=server)
            if os.path.exists(override):
                area, _ = self.extract(override)
            override = self.get_file('COLOR', server=server)
            if os.path.exists(override):
                _, color = self.extract(override)
            override = self.get_file('BUTTON', server=server)
            if os.path.exists(override):
                button, _ = self.extract(override)

            self.area[server] = area
            self.color[server] = color
            self.button[server] = button
            self.file[server] = file
        else:
            logger.attr(server, f'{self.name} not found, use cn server assets')
            self.area[server] = self.area['cn']
            self.color[server] = self.color['cn']
            self.button[server] = self.button['cn']
            self.file[server] = self.file['cn']

    @property
    def expression(self):
        return '%s = Button(area=%s, color=%s, button=%s, file=%s)' % (
            self.name, self.area, self.color, self.button, self.file)


class TemplateExtractor(ImageExtractor):
    # def __init__(self, module, file, config):
    #     """
    #     Args:
    #         module(str):
    #         file(str): xxx.png
    #         config(AzurLaneConfig):
    #     """
    #     self.module = module
    #     self.file = file
    #     self.config = config
    @staticmethod
    def extract(file):
        image = load_image(file)
        bbox = get_bbox(image)
        mean = get_color(image=image, area=bbox)
        mean = tuple(np.rint(mean).astype(int))
        return bbox, mean

    @property
    def expression(self):
        return '%s = Template(file=%s)' % (
            self.name, self.file)
        # return '%s = Template(area=%s, color=%s, button=%s, file=\'%s\')' % (
        #     self.name, self.area, self.color, self.button,
        #     self.config.ASSETS_FOLDER + '/' + self.module + '/' + self.name + '.png')


class ModuleExtractor:
    def __init__(self, name):
        self.name = name
        self.folder = os.path.join(NikkeConfig.ASSETS_FOLDER, 'story_event', name)
        # print(os.path.join(MODULE_FOLDER, self.name))
        '''
            os.path.join(MODULE_FOLDER, self.name)
            ./module\event_1
            
            self.folder
            ./assets\story_event\event_1
        '''

    @staticmethod
    def split(file):
        name, ext = os.path.splitext(file)
        name, sub = os.path.splitext(name)
        return name, sub, ext

    def is_base_image(self, file):
        _, sub, _ = self.split(file)
        return sub == ''

    @property
    def expression(self):
        exp = []
        for file in os.listdir(self.folder):
            if file[0].isdigit():
                continue
            if file.startswith('TEMPLATE_'):
                exp.append(TemplateExtractor(module=self.name, file=file).expression)
                continue
            # if file.startswith('OCR_'):
            #     exp.append(OcrExtractor(module=self.name, file=file, config=self.config).expression)
            #     continue
            if self.is_base_image(file):
                exp.append(ImageExtractor(module=self.name, file=file).expression)
                continue

        logger.info('Module: %s(%s)' % (self.name, len(exp)))
        exp = IMPORT_EXP + exp
        return exp

    def write(self):
        folder = os.path.join(MODULE_FOLDER, 'story_event', self.name)
        if not os.path.exists(folder):
            os.mkdir(folder)
        with open(os.path.join(folder, BUTTON_FILE), 'w', newline='') as f:
            for text in self.expression:
                f.write(text + '\n')


def worker(module):
    me = ModuleExtractor(name=module)
    me.write()


class AssetExtractor:
    def __init__(self):
        logger.info('Event Assets extract')
        event = NikkeConfig.EVENTS[0].get('event_id')
        modules = [m for m in os.listdir(NikkeConfig.ASSETS_FOLDER + '/story_event') if
                   os.path.isdir(os.path.join(NikkeConfig.ASSETS_FOLDER + '/story_event', m)) and m == event]

        process_map(worker, modules)


if __name__ == '__main__':
    ae = AssetExtractor()
