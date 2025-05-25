import json
import random
import time
from functools import cached_property
from typing import Any, Dict, List

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
from module.special_arena.assets import *
from module.ui.assets import SPECIAL_ARENA_CHECK, ARENA_GOTO_SPECIAL_ARENA
from module.ui.page import page_arena
from module.ui.ui import UI


class SpecialArenaIsUnavailable(Exception):
    pass


class SpecialArena(UI):
    @cached_property
    def button(self):
        return [(590, 800), (590, 950), (590, 1100)]
    
    @cached_property
    def coordinate_config(self) -> list[dict]:
        """
        返回战力、等级识别区域
        """
        return [
            {
                "Power": (376, 736, 455, 767),
                "CommanderLevel": (72, 801, 111, 817),
                "SynchroLevel": (392, 836, 414, 855)
            },
            {
                "Power": (376, 886, 455, 917),
                "CommanderLevel": (72, 951, 111, 967),
                "SynchroLevel": (392, 986, 414, 1005)
            },
            {
                "Power": (376, 1036, 455, 1067),
                "CommanderLevel": (72, 1101, 111, 1117),
                "SynchroLevel": (392, 1136, 414, 1155)
            }
        ]
    
    FIELD_LETTERS = {
        "Power": (107, 107, 107),
        "CommanderLevel": (222, 222, 222),
        "SynchroLevel": (255, 255, 255)
    }
    
    @property
    def free_opportunity_remain(self) -> bool:
        # 免费票
        result = FREE_OPPORTUNITY_CHECK.appear_on(self.device.image, 20)
        if result:
            logger.info(f"[Free opportunities remain] {result}")
        return result

    @cached_property
    def own_power(self) -> int:
        # 获取战力
        area = _area_offset(OWN_POWER_CHECK.area, (20, -2, 70, 2))
        OWN_POWER = Digit(
            [area],
            name="OWN_POWER",
            letter=(247, 247, 247),
            threshold=128,
            lang="cnocr_23_num_fc",
        )
        return int(OWN_POWER.ocr(self.device.image))
    
    def opponent_info(self, area: tuple, field_name: str) -> int:        
        letter = self.FIELD_LETTERS[field_name]
        OPPONENT_INFO = Digit(
            [area],
            name="OPPONENT_INFO",
            letter=letter,
            threshold=128,
            lang="cnocr_23_num_fc"
        )
        return int(OPPONENT_INFO.ocr(self.device.image))

    def opponents_data(self) -> List[Dict[str, Any]]:
        """获取对手数据"""
        results = []
        for group_idx, group_config in enumerate(self.coordinate_config, start=1):
            group_result = {
                "id": group_idx,  # 对手位置序号（1=第一位，2=第二位，3=第三位）
                "data": {
                    "Power": self.opponent_info(group_config["Power"], "Power"),
                    "CommanderLevel": self.opponent_info(group_config["CommanderLevel"], "CommanderLevel"),
                    "SynchroLevel": self.opponent_info(group_config["SynchroLevel"], "SynchroLevel"),
                    "Ranking": group_idx
                }
            }
            logger.info(f"Find opponent {group_idx}: {group_result['data']}")
            results.append(group_result)
        return results

    def select_strategy(self) -> Dict:
        """根据选择策略返回相应的对手"""
        all_opponents = self.opponents_data()
        weights = json.loads(self.config.OpponentSelection_SortingWeight)

        # 提取原始数据
        dimensions = {
            'Power': [opp['data']['Power'] for opp in all_opponents],
            'CommanderLevel': [opp['data']['CommanderLevel'] for opp in all_opponents],
            'SynchroLevel': [opp['data']['SynchroLevel'] for opp in all_opponents],
            'Ranking': [opp['data']['Ranking'] for opp in all_opponents]
        }
        # 归一化处理
        normalized = {k: self._normalize(v) for k, v in dimensions.items()}
        normalized['Ranking'] = [1 - val for val in normalized['Ranking']]
        # 计算综合得分
        for i, opp in enumerate(all_opponents):
            score = sum(
                normalized[dim][i] * weights[dim]
                for dim in ['Power', 'CommanderLevel', 'SynchroLevel', 'Ranking']
            )
            opp['score'] = score

        sorted_data = sorted(all_opponents, key=lambda x: x['score'], reverse=True)

        # 选择策略
        if self.config.OpponentSelection_SelectionStrategy == 'Max':
            return sorted_data[0]
        elif self.config.OpponentSelection_SelectionStrategy == 'Min':
            return sorted_data[-1]
        elif self.config.OpponentSelection_SelectionStrategy == 'Middle':
            return sorted_data[len(sorted_data) // 2] if sorted_data else None

    def _normalize(self, values: List[float]) -> List[float]:
        """数据归一化到[0,1]区间（处理全等值情况）"""
        min_val = min(values)
        max_val = max(values)
        if max_val == min_val:
            return [0.5] * len(values)  # 所有值相同时返回中性值
        return [(v - min_val) / (max_val - min_val) for v in values]

    def start_competition(self, skip_first_screenshot=True):
        logger.hr("Start a competition")

        confirm_timer = Timer(1, count=5).start()
        click_timer = Timer(0.3)
        click_timer_2 = Timer(5)

        already_start = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                    not already_start
                    and click_timer.reached()
                    and click_timer_2.reached()
                    and self.free_opportunity_remain
            ):
                # 根据策略选择
                opponent_id =  3
                if self.config.OpponentSelection_Enable:
                    opponent_id = self.select_strategy()["id"]
                opponent =  self.button[opponent_id-1]
                logger.info(f"Secect opponent {opponent_id}")       
                
                self.device.click_minitouch(opponent[0], opponent[1])
                logger.info(
                    "Click %s @ %s"
                    % (point2str(opponent[0], opponent[1]), "START_COMPETITION")
                )
                confirm_timer.reset()
                click_timer.reset()
                click_timer_2.reset()
                continue

            if click_timer.reached() and self.appear_then_click(SKIP, offset=(5, 5), interval=1):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if (
                    not already_start
                    and click_timer.reached()
                    and self.appear_then_click(
                INTO_COMPETITION, offset=(30, 30), interval=5, static=False
            )
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear(
                    END_COMPETITION, offset=5, interval=2
            ):
                logger.info("Click %s @ %s" % (point2str(100, 100), "END_COMPETITION"))
                self.device.handle_control_check(END_COMPETITION)
                self.device.click_minitouch(100, 100)
                already_start = True
                confirm_timer.reset()
                click_timer.reset()
                continue

            if (
                    already_start
                    and self.appear(SPECIAL_ARENA_CHECK, offset=(10, 10), static=False)
                    and confirm_timer.reached()
            ):
                break

        if self.free_opportunity_remain:
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.start_competition()

    def ensure_into_special_arena(self, skip_first_screenshot=True):
        confirm_timer = Timer(2, count=3).start()
        click_timer = Timer(0.3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(NEXT_SEASON, offset=(50, 50)):
                raise SpecialArenaIsUnavailable

            if click_timer.reached() and self.appear_then_click(
                    ARENA_GOTO_SPECIAL_ARENA, offset=(30, 30), interval=5, static=False
            ):
                confirm_timer.reset()
                click_timer.reset()
                continue
            
            if (
                    self.appear(SPECIAL_ARENA_CHECK, offset=(10, 10), static=False)
                    and confirm_timer.reached()
            ):
                break
        
        if self.free_opportunity_remain:
            self.start_competition()
        else:
            logger.info("There are no free opportunities")

    def run(self):
        self.ui_ensure(page_arena)
        try:
            self.ensure_into_special_arena()
        except SpecialArenaIsUnavailable:
            pass
        self.config.task_delay(server_update=True)
