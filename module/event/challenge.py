from module.base.timer import Timer
from module.base.utils import get_button_by_location
from module.challenge.assets import *
from module.event.assets import *
from module.event.base import ChallengeNotFoundError, EventBase
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING
from module.tribe_tower.assets import OPERATION_FAILED
from module.ui.assets import FIGHT_QUICKLY_CHECK


class EventChallenge(EventBase):
    def challenge(self, skip_first_screenshot=True):
        logger.hr('START EVENT CHALLENGE', 2)
        click_timer = Timer(0.3)

        # 进入挑战页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.CHALLENGE, offset=10, interval=5)
            ):
                click_timer.reset()
                continue

            if self.appear(CHALLENGE_CHECK, offset=10):
                self.device.sleep(2)
                break

        self.device.screenshot()
        # 判断新挑战关卡
        challenge_stages = TEMPLATE_CHALLENGE_STAGE.match_multi(
            self.device.image, similarity=0.7, name='CHALLENGE_STAGE'
        )
        if challenge_stages:
            logger.info('Finf new challenge stage')
            self.device.click(challenge_stages[0])
        else:
            # 判断已经打过的挑战关卡
            clear_stages = TEMPLATE_CLEAR_STAGE.match_multi(self.device.image, name='CLEAR_STAGE')
            if not clear_stages:
                raise ChallengeNotFoundError
            # 取一个y坐标最大的关卡
            stage = get_button_by_location(clear_stages, coord='y', order='descending')
            logger.info('Finf cleared challenge stage')
            self.device.click(stage)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 已经挑战过，返回挑战列表
            if (
                self.appear(CHALLENGE_STAGE_CHECK, offset=10)
                and self.appear(CHALLENGE_QUICKLY_DISABLE, threshold=10)
                and self.appear(CHALLENGE_BATTLE_DONE, threshold=10)
                and self.appear_then_click(CHALLENGE_CANCEL, offset=10, interval=1)
            ):
                break

            # 战斗结束
            if click_timer.reached() and self.appear(END_FIGHTING, offset=30):
                while 1:
                    self.device.screenshot()
                    if not self.appear(END_FIGHTING, offset=30):
                        click_timer.reset()
                        break
                    if self.appear_then_click(END_FIGHTING, offset=30, interval=1):
                        click_timer.reset()
                        continue
                break

            # 快速战斗
            if (
                click_timer.reached()
                and self.appear(CHALLENGE_STAGE_CHECK, offset=10)
                and self.appear(CHALLENGE_BATTLE, threshold=10)
                and self.appear_then_click(CHALLENGE_QUICKLY_ENABLE, threshold=20, interval=1)
            ):
                click_timer.reset()
                continue

            # 使用票进行战斗
            if (
                click_timer.reached()
                and self.appear(FIGHT_QUICKLY_CHECK, offset=30)
                and self.appear_then_click(CHALLENGE_QUICK_TICKET, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 进入战斗
            if (
                click_timer.reached()
                and self.appear(CHALLENGE_STAGE_CHECK, offset=10)
                and self.appear(CHALLENGE_QUICKLY_DISABLE, threshold=10)
                and self.appear_then_click(CHALLENGE_BATTLE, threshold=10, interval=1)
            ):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_SHOOT, offset=10, threshold=0.9, interval=5):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(AUTO_BURST, offset=10, threshold=0.9, interval=5):
                click_timer.reset()
                continue

            if self.appear(OPERATION_FAILED, offset=10):
                logger.error('Challenge stage fight failed')
                raise RequestHumanTakeover

        self.back_to_event()
        logger.info('Event challenge done')
