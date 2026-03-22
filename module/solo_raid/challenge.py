from functools import cached_property

from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Digit
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING, PAUSE
from module.solo_raid.assets import *
from module.ui.assets import MAIN_CHECK
from module.ui.ui import UI


class SoloRaidChallenge(UI):
    @cached_property
    def teams(self):
        return [RAID_TEAM_1, RAID_TEAM_2, RAID_TEAM_3, RAID_TEAM_4, RAID_TEAM_5]

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
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            self.challenge_raid()
        else:
            logger.warning('There are no free opportunities for challenge mode')

    def challenge_raid(self, skip_first_screenshot=True):
        """挑战模式战斗执行"""
        logger.hr('Start a challenge raid')
        team_change_timer = Timer(1).start()

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

            # 选择队伍
            if self.appear(FIGHT_HISTORY, offset=10):
                if self.appear(ENTER_FIGHT, offset=10) and self.appear_then_click(
                    ENTER_FIGHT, threshold=10, interval=1
                ):
                    team_change_timer.reset()
                    continue

                if team_change_timer.reached():
                    current_team = -1
                    for i, team in enumerate(self.teams):
                        if not self.appear(team, threshold=10):
                            current_team = i
                            break

                    if current_team != -1:
                        if current_team < 4:
                            logger.info(f'Team {current_team + 1} is not valid, switch to Team {current_team + 2}')
                            self.device.click(self.teams[current_team + 1])
                            self.device.sleep(0.5)
                            team_change_timer.reset()
                            continue
                        else:
                            logger.warning('No valid team found')
                            break
            else:
                team_change_timer.reset()

            # 自动射击和爆裂
            if self.appear_then_click(AUTO_SHOOT, offset=10, threshold=0.9, interval=5):
                continue
            if self.appear_then_click(AUTO_BURST, offset=10, threshold=0.9, interval=5):
                continue
            # 红圈
            if self.config.Optimization_AutoRedCircle and self.appear(PAUSE, offset=(5, 5)):
                if self.handle_red_circles():
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
            if self.appear(FIGHT_HISTORY, offset=10) and self.appear(RAID_TEAM_2, threshold=10):
                logger.info('Challenge raid end team one')
                continue

            # 结算弹窗
            if self.appear(ENEMY_DEFEATED, offset=150) and self.appear(ENEMY_DEFEATED_CONFIRM, offset=150):
                logger.info('Challenge raid end team all')

                while 1:
                    self.device.screenshot()
                    if self.appear(SOLO_RAID_CHECK, offset=30):
                        break
                    if self.appear_then_click(ENEMY_DEFEATED_CONFIRM, offset=150, interval=1):
                        continue
                break

        # 如果次数仍大于0且伤害依旧为0，继续挑战
        if self.free_opportunity_remain:
            if not self.challenge_damage_is_zero:
                logger.info('Challenge damage is recorded. Challenge mode complete.')
            else:
                logger.warning('Challenge mode complete. But damage is not recorded.')
