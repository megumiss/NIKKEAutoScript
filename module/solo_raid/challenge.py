from module.logger import logger
from module.ocr.ocr import Digit
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING
from module.solo_raid.assets import *
from module.ui.assets import MAIN_CHECK
from module.ui.ui import UI


class SoloRaidChallenge(UI):
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

    @property
    def challenge_damage_is_zero(self) -> bool:
        """
        判断挑战模式当前伤害是否为0
        """
        if self.appear(CHALLENGE_DAMAGE_ZERO, offset=(30, 30)):
            return True
        else:
            return False

    def ensure_into_challenge(self, skip_first_screenshot=True):
        """检查并进入挑战模式"""
        logger.hr('CHALLENGE MODE CHECK')

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(MAIN_CHECK, offset=10) and self.appear_then_click(
                SOLO_RAID, offset=10, interval=3, static=False
            ):
                logger.info('Enter solo raid')
                continue

            # 检查挑战
            if self.appear(SOLO_RAID_CHECK, offset=(10, 10)):
                if self.appear(STAGE_CHALLENGE, offset=(30, 30)):
                    # 挑战界面
                    break
                else:
                    # 挑战未开启
                    logger.warning('Challenge mode not reached')
                    return

        # 检查伤害是否为0
        if not self.challenge_damage_is_zero:
            logger.info('Challenge damage is already recorded. Skip challenge mode.')
            return

        # 检查挑战次数
        if self.free_opportunity_remain:
            self.challenge_raid()

        else:
            logger.warning('There are no free opportunities for challenge mode')

    def challenge_raid(self, skip_first_screenshot=True):
        """挑战模式战斗执行"""
        logger.hr('Start a challenge raid')

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 点击挑战
            if self.appear(SOLO_RAID_CHECK, offset=10) and self.appear_then_click(CHALLENGE, threshold=10, interval=2):
                continue

            # 挑战确认
            if self.appear(CHALLENGE_CONFIRM_CHECK, offset=10) and self.appear_then_click(
                CHALLENGE_CONFIRM, offset=10, interval=1
            ):
                continue

            # 开始战斗
            if self.appear(FIGHT_HISTORY, offset=10) and self.appear_then_click(ENTER_FIGHT, offset=10, interval=1):
                continue

            # 自动射击和爆裂
            if self.appear_then_click(AUTO_SHOOT, offset=10, threshold=0.9, interval=5):
                continue

            if self.appear_then_click(AUTO_BURST, offset=10, threshold=0.9, interval=5):
                continue

            # 结束
            if self.appear(END_FIGHTING, offset=30):
                while 1:
                    self.device.screenshot()
                    if not self.appear(END_FIGHTING, offset=30):
                        break
                    if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                        continue
                continue

            # 回到队伍选择界面
            if self.appear(FIGHT_HISTORY, offset=10) and not self.appear(ENTER_FIGHT, offset=10):
                logger.info('Challenge raid end one')
                break

        # 选择下一个队伍
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(FIGHT_HISTORY, offset=10):
                break

        # 选择下一个队伍

        # 如果次数仍大于0且伤害依旧为0，继续挑战
        if self.free_opportunity_remain:
            if not self.challenge_damage_is_zero:
                logger.info('Challenge damage is recorded. Challenge mode complete.')
                return
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.challenge_raid()
