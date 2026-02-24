from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Digit
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING
from module.solo_raid.assets import *
from module.ui.ui import UI


class SoloRaidChallenge(UI):
    @property
    def challenge_damage_is_zero(self) -> bool:
        """
        判断挑战模式当前伤害是否为0
        """
        try:
            model_type = self.config.Optimization_OcrModelType
            CURRENT_DAMAGE = Digit(
                [CURRENT_DAMAGE_AREA.area],  # 请确保在assets.py中定义了 CURRENT_DAMAGE_AREA
                name='CURRENT_DAMAGE',
                model_type=model_type,
                lang='ch',
            )
            damage = int(CURRENT_DAMAGE.ocr(self.device.image)['text'])
            logger.info(f'[Current Challenge Damage] {damage}')
            return damage == 0
        except Exception as e:
            logger.warning(f'Failed to read damage or damage is empty: {e}')
            return False

    def ensure_into_challenge(self, skip_first_screenshot=True):
        """检查并进入挑战模式"""
        logger.hr('CHALLENGE MODE CHECK')
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 检查当前是否在第七关界面，不是则直接退出挑战逻辑
            if self.appear(SOLO_RAID_CHECK, offset=10) and not self.appear(STAGE_SEVEN, offset=(30, 30)):
                logger.info('Current stage is not Stage 7. Skip challenge mode.')
                return

            # 如果在第七关，点击 STAGE_CHALLENGE_SWITCH 切换到挑战模式
            if (
                click_timer.reached()
                and self.appear(SOLO_RAID_CHECK, offset=10)
                and self.appear(STAGE_SEVEN, offset=(30, 30))
                and self.appear_then_click(STAGE_CHALLENGE_SWITCH, offset=10, interval=2)
            ):
                click_timer.reset()
                continue

            # 确认已经进入挑战模式页面
            if self.appear(STAGE_CHALLENGE, offset=(30, 30)):
                break

        # 1. 检查伤害是否为0
        if not self.challenge_damage_is_zero:
            logger.info('Challenge damage is already recorded. Skip challenge mode.')
            return

        # 2. 检查挑战次数 (free_opportunity_remain 由主类共享)
        if self.free_opportunity_remain:
            self.challenge_raid()
        else:
            logger.warning('There are no free opportunities for challenge mode')

    def challenge_raid(self, skip_first_screenshot=True):
        """挑战模式战斗执行 (无扫荡逻辑)"""
        logger.hr('Start a challenge raid')
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 点击挑战
            if (
                click_timer.reached()
                and self.appear(SOLO_RAID_CHECK, offset=10)
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

            # 自动射击和爆裂
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

        # 回到单人突击界面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(SOLO_RAID_CHECK, offset=10):
                break

        # 如果次数仍大于0且伤害依旧为0，继续挑战
        if self.free_opportunity_remain:
            if not self.challenge_damage_is_zero:
                logger.info('Challenge damage is recorded. Challenge mode complete.')
                return
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.challenge_raid()