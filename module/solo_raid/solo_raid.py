from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Digit
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING
from module.solo_raid.assets import *
from module.ui.assets import FIGHT_QUICKLY_CHECK, FIGHT_QUICKLY_MAX, FIGHT_QUICKLY_MIN, MAIN_CHECK
from module.ui.page import page_main
from module.ui.ui import UI


class NoOpportunityRemain(Exception):
    pass


class SoloRaidIsUnavailable(Exception):
    pass


class SoloRaid(UI):
    @property
    def free_remain(self) -> int:
        model_type = self.config.Optimization_OcrModelType
        FREE_REMAIN = Digit(
            [FREE_OPPORTUNITY_CHECK.area],
            name='FREE_REMAIN',
            model_type=model_type,
            lang='ch',
        )
        return int(FREE_REMAIN.ocr(self.device.image)['text'])

    @property
    def free_opportunity_remain(self) -> bool:
        # result = self.appear(FREE_OPPORTUNITY_CHECK, offset=10, threshold=0.8)
        if self.free_remain:
            logger.info(f'[Free opportunities remain] {self.free_remain}')
        return self.free_remain

    def ensure_into_soloraid(self, skip_first_screenshot=True):
        """进入单人突击"""
        logger.hr('SOLO RAID START')
        click_timer = Timer(0.3)
        confirm_timer = Timer(15, count=10).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 结算
            if click_timer.reached() and self.appear_then_click(RAID_RESULT, offset=10, interval=3, static=False):
                logger.info('Solo raid has end')
                raise SoloRaidIsUnavailable

            if (
                click_timer.reached()
                and self.appear(MAIN_CHECK, offset=10)
                and self.appear_then_click(SOLO_RAID, offset=10, interval=3, static=False)
            ):
                logger.info('Enter solo raid')
                continue

            # 选择第七关
            if (
                click_timer.reached()
                and self.appear(SOLO_RAID_CHECK, offset=(10, 10))
                and self.appear(STAGE_CHALLENGE, offset=(30, 30))
                and self.appear_then_click(STAGE_SEVEN_SWITCH, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            if self.appear(SOLO_RAID_CHECK, offset=(10, 10)) and not self.appear(STAGE_CHALLENGE, offset=(30, 30)):
                break

            if confirm_timer.reached():
                logger.error('Solo raid not found')
                raise SoloRaidIsUnavailable

        if self.free_opportunity_remain:
            self.solo_raid()
        else:
            logger.warning('There are no free opportunities')
            raise NoOpportunityRemain

    def solo_raid(self, skip_first_screenshot=True):
        logger.hr('Start a solo raid')
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 第七关扫荡
            if (
                click_timer.reached()
                and self.appear(SOLO_RAID_CHECK, offset=10)
                and self.appear(STAGE_SEVEN, offset=(30, 30))
                and self.appear_then_click(CHALLENGE_QUICKLY_ENABLE, threshold=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 扫荡票max
            if (
                click_timer.reached()
                and self.appear(FIGHT_QUICKLY_CHECK, offset=10)
                and self.appear_then_click(FIGHT_QUICKLY_MAX, offset=30, threshold=0.99, interval=1)
            ):
                click_timer.reset()
                continue

            # 扫荡确定
            if (
                click_timer.reached()
                and self.appear(FIGHT_QUICKLY_CHECK, offset=10)
                and self.appear(FIGHT_QUICKLY_MIN, offset=30, threshold=0.99)
                and self.appear_then_click(CHALLENGE_QUICKLY_CONFIRM, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 挑战
            if (
                click_timer.reached()
                and self.appear(SOLO_RAID_CHECK, offset=10)
                and self.appear(CHALLENGE_QUICKLY_DISABLE, threshold=10)
                and self.appear_then_click(CHALLENGE, threshold=10, interval=2)
            ):
                click_timer.reset()
                continue

            # 挑战确认
            if (
                click_timer.reached()
                and self.appear(CHALLENGE_CONFIRM_CHECK, offset=10)
                and self.appear_then_click(CHALLENGE_CONFIRM, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 开始战斗
            if (
                click_timer.reached()
                and self.appear(FIGHT_HISTORY, offset=10)
                and self.appear_then_click(ENTER_FIGHT, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=10, threshold=0.9, interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=10, threshold=0.9, interval=5):
                click_timer.reset()
                continue

            # 结束
            if click_timer.reached() and self.appear(END_FIGHTING, offset=30):
                while 1:
                    self.device.screenshot()
                    if not self.appear(END_FIGHTING, offset=30):
                        click_timer.reset()
                        break
                    if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                        click_timer.reset()
                        continue
                click_timer.reset()
                continue

            # 结算弹窗
            if (
                click_timer.reached()
                and self.appear(ENEMY_DEFEATED, offset=10)
                and self.appear_then_click(ENEMY_DEFEATED_CONFIRM, offset=10, interval=1)
            ):
                click_timer.reset()
                break

            # 扫荡结束
            if (
                click_timer.reached()
                and self.appear(SOLO_RAID_CHECK, offset=10)
                and self.appear(STAGE_SEVEN, offset=(30, 30))
                and self.appear(CHALLENGE_QUICKLY_DISABLE, threshold=10)
                and not self.appear(CHALLENGE, threshold=10)
            ):
                click_timer.reset()
                break

        # 进入单人突击界面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(SOLO_RAID_CHECK, offset=10):
                break

        if self.free_opportunity_remain:
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.solo_raid()
        else:
            logger.info('There are no free opportunities')
            raise NoOpportunityRemain

    def run(self):
        try:
            self.ui_ensure(page_main)
            self.ensure_into_soloraid()
        except SoloRaidIsUnavailable:
            pass
        except NoOpportunityRemain:
            pass

        self.config.task_delay(server_update=True)
