import re
from functools import cached_property
from typing import Optional

from module.base.langs import Langs
from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING, PAUSE
from module.ui.assets import MAIN_CHECK
from module.ui.page import page_main
from module.ui.ui import UI
from module.union_raid.assets import *


class NoOpportunityRemain(Exception):
    pass


class UnionRaidIsUnavailable(Exception):
    pass


class UnionRaid(UI):
    # 读「每日游戏次数」的重试时长，读不到就每秒重拍一次
    DAILY_PLAY_COUNT_TIMEOUT = 10

    @cached_property
    def teams(self):
        return [RAID_TEAM_1, RAID_TEAM_2, RAID_TEAM_3, RAID_TEAM_4, RAID_TEAM_5]

    @property
    def free_remain(self) -> Optional[int]:
        """
        读取「每日游戏次数」，只认「每日游戏次数」标签和 `x/y` 数字都读到的结果。

        次数条没显示、页面不对或 OCR 失败时重试，仍读不到返回 None（不能当成 0 次）。
        """
        model_type = self.config.Optimization_OcrModelType
        daily_play_count = Ocr(
            [FREE_OPPORTUNITY_CHECK.area],
            name='FREE_REMAIN',
            model_type=model_type,
            lang='ch',
        )
        timer = Timer(self.DAILY_PLAY_COUNT_TIMEOUT, count=2).start()
        while 1:
            text = daily_play_count.ocr(self.device.image)['text']
            # 数字里的斜杠可能被识别成竖线
            match = re.search(r'(\d+)\s*/\s*(\d+)', text.replace('|', '/'))
            if match and Langs.UNION_RAID_DAILY_PLAY_COUNT in text:
                return int(match.group(1))
            if timer.reached():
                logger.warning(f'Daily play count not found: "{text}"')
                return None
            self.device.sleep(1)
            self.device.screenshot()

    @property
    def free_opportunity_remain(self) -> Optional[int]:
        remain = self.free_remain
        if remain:
            logger.info(f'[Free opportunities remain] {remain}')
        return remain

    def ensure_into_union(self, skip_first_screenshot=True):
        """进入联盟"""
        logger.hr('UNION RAID START')
        confirm_timer = Timer(10, count=10).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(MAIN_CHECK, offset=10) and self.appear_then_click(UNION, offset=70, interval=3):
                continue

            # 联盟主页
            if self.appear(UNION_CHECK, offset=10):
                logger.info('Enter union')
                break

            if confirm_timer.reached():
                logger.error('Union raid has end')
                raise UnionRaidIsUnavailable

    def ensure_into_unionraid(self, skip_first_screenshot=True):
        """进入联盟突袭"""
        confirm_timer = Timer(3, count=3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(UNION_CHECK, offset=10) and self.appear_then_click(UNION_RAID, offset=10, interval=3):
                continue

            # 结算弹窗
            if self.appear(ENEMY_DEFEATED, offset=10, static=False) and self.appear_then_click(
                ENEMY_DEFEATED_CONFIRM_SKIP, offset=10, static=False, interval=2
            ):
                continue

            # 结算弹窗
            if self.appear(ENEMY_DEFEATED, offset=10, static=False) and self.appear_then_click(
                ENEMY_DEFEATED_CONFIRM, offset=10, static=False, interval=2
            ):
                continue

            # 突袭主页
            if self.appear(UNION_RAID_CHECK, offset=10):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    logger.info('Enter union raid')
                    self.device.sleep(1)
                    break
            else:
                confirm_timer.clear()

        if self.free_opportunity_remain:
            self.union_raid()
        else:
            logger.warning('There are no free opportunities')
            raise NoOpportunityRemain

    def union_raid(self, skip_first_screenshot=True):
        logger.hr('Start a union raid')

        teamindex = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if teamindex == 5:
                logger.warning('There are no team to fight')
                raise NoOpportunityRemain

            # 点击莱彻
            if self.appear(UNION_RAID_CHECK, offset=10) and self.appear_then_click(
                UNION_RAID_SELECT, offset=(50, 10), interval=2
            ):
                continue

            # 切换队伍
            if self.appear(UNION_RAID_ENEMY_CHECK, offset=10) and self.appear_then_click(
                self.teams[teamindex], threshold=10, interval=1
            ):
                self.device.sleep(0.5)
                continue

            # 禁止提示
            if (
                self.appear(UNION_RAID_ENEMY_CHECK, offset=10)
                and not self.appear_then_click(self.teams[teamindex], threshold=10)
                and self.appear(ENTER_FIGHT_DISABLE, threshold=10)
            ):
                teamindex += 1
                continue

            # 进入战斗
            if self.appear(UNION_RAID_ENEMY_CHECK, offset=10) and self.appear_then_click(
                ENTER_FIGHT, threshold=10, interval=2
            ):
                continue

            if self.appear_then_click(AUTO_SHOOT, offset=10, threshold=0.9, interval=5):
                continue

            if self.appear_then_click(AUTO_BURST, offset=10, threshold=0.9, interval=5):
                continue

            # 红圈
            if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                if self.handle_red_circles():
                    continue

            # 战斗结束
            if self.appear(END_FIGHTING, offset=30):
                while 1:
                    self.device.screenshot()
                    if not self.appear(END_FIGHTING, offset=30):
                        break
                    if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                        continue
                logger.info('Complete a union raid')
                break

        confirm_timer = Timer(3, count=3)
        while 1:
            self.device.screenshot()

            # 结算弹窗
            if self.appear(ENEMY_DEFEATED, offset=10, static=False) and self.appear_then_click(
                ENEMY_DEFEATED_CONFIRM_SKIP, offset=10, static=False, interval=2
            ):
                continue

            # 结算弹窗
            if self.appear(ENEMY_DEFEATED, offset=10, static=False) and self.appear_then_click(
                ENEMY_DEFEATED_CONFIRM, offset=10, static=False, interval=2
            ):
                continue

            # 突袭主页
            if self.appear(UNION_RAID_CHECK, offset=10):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    self.device.sleep(1)
                    break
            else:
                confirm_timer.clear()

        if self.free_opportunity_remain:
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.union_raid()
        else:
            logger.info('There are no free opportunities')
            raise NoOpportunityRemain

    def run(self):
        try:
            self.ui_ensure(page_main)
            self.ensure_into_union()
            self.ensure_into_unionraid()
        except UnionRaidIsUnavailable:
            pass
        except NoOpportunityRemain:
            pass

        self.config.task_delay(schedule=True)
