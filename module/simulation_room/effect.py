import time
from functools import cached_property
import numpy as np
import cv2

from module.base.timer import Timer
from module.base.utils import (
    _area_offset,
    crop,
    extract_letters,
    find_letter_area,
    float2str,
    point2str,
)
from module.logger import logger
from module.ocr.ocr import Digit
from module.simulation_room.assets import *
from module.ui.ui import UI

EFFECT_RARITY_SET = {"SSR", "SR", "R"}
EFFECT_MAPPING_SET = {
    "高品质粉末",
    "隐形粉",
    "快速充电器",
    "连结AMO",
    "引流转换器",
    "聚焦瞄准镜",
    "重启载体",
    "艾薇拉粒子干扰丝",
    "自动对焦眼球",
    "控制引导器",
    "冲击引流器",
    "辅助发电机",
    "小型离子束屏障",
    "治愈喷雾弹头",
    "快速换弹程序"
}
CLASS_AND_MANUFACTURER_SET = {
    "ATTACKER": TEMPLATE_CLASS_ATTACKER,
    "DEFENDER": TEMPLATE_CLASS_DEFENDER,
    "SUPPORTER": TEMPLATE_CLASS_SUPPORTER,
    "ESYSION": TEMPLATE_MANUFACTURER_ESYSION,
    "MISSILIS": TEMPLATE_MANUFACTURER_MISSILIS,
    "PILGRIM": TEMPLATE_MANUFACTURER_PILGRIM,
    "TETRA": TEMPLATE_MANUFACTURER_TETRA
}
# EFFECT_MAPPING_SET = {
#     "高品质粉末": "HighQualityPowder",
#     "隐形粉": "HiddenPowder",
#     "快速充电器": "QuickCharger",
#     "连结AMO": "ChainAmmo",
#     "引流转换器": "DrainConverter",
#     "聚焦瞄准镜": "FocusScope",
#     "重启载体": "RestartSupporter",
#     "艾薇拉粒子干扰丝": "AlvaParticleChaff",
#     "自动对焦眼球": "AutoFocusOculus",
#     "控制引导器": "HomingGuider",
#     "冲击引流器": "ImpactInducer",
#     "辅助发电机": "SubGenerator",
#     "小型离子束屏障": "CompactBeamBarrier",
#     "治愈喷雾弹头": "HealSprayBullet",
#     "快速换弹程序": "QuickReloadSequence"
# }

class EffectBase(UI):
    def __init__(self, data, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data = data

class EffectAnalyzer(EffectBase):
    def build_effect_struct(self):
        """
        effect列表，格式: [{rarity, effect, position, applies}]
        """
        effects = []
        effect = None
        
        for item in self.data:
            text = item["text"].strip()
            # 匹配稀有度
            if text in EFFECT_RARITY_SET:
                effect = {
                    "rarity": text,
                    "effect": None,
                    "position": None,
                    "applies": None
                }
            
            # 匹配词条
            if effect is not None and text in EFFECT_MAPPING_SET:
                #effect["effect"] = EFFECT_MAPPING_SET[text]
                effect["effect"] = text
                effect["position"] = item["position"]
            
            # 获取适用对象
            if effect is not None and "适用对象" in text:
                area = self.adjust_position(item["position"])
                effect["applies"] = self.predict_enemy_genre(crop(self.device.image, area))
        
            if effect is not None and effect["effect"] is not None:
                # TODO 输出effect日志
                print(f"当前effect '{effect}'")
                effects.append(effect)
                effect = None
        
        return effects
    
    @staticmethod
    def adjust_position(position):
        """
        获得 适用对象 的范围, 包括图标
        """
        pos = np.array(position, dtype=np.float32)
        
        x1, y1 = pos[0][0], pos[0][1]
        _, y2 = pos[2][0], pos[2][1]
        new_y1 = y1 - 10
        new_y2 = y2 + 10
        new_x2 = 635.0

        return (x1, new_y1, new_x2, new_y2)

    @staticmethod
    def predict_enemy_genre(image):
        for name, template in CLASS_AND_MANUFACTURER_SET.items():
            if template.match(image):
                return name