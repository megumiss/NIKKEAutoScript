from functools import cached_property
from module.exception import OperationFailed
from module.base.decorator import Config
from module.base.timer import Timer
from module.base.utils import get_button_by_location
from module.logger import logger
from module.ui.ui import UI
from module.ui.assets import GOTO_BACK, MAIN_CHECK
from module.simulation_room.assets import AUTO_SHOOT, AUTO_BURST, END_FIGHTING
from module.tribe_tower.assets import OPERATION_FAILED
from module.challenge.assets import *
from module.ui.page import *
# 活动引用
from module.story_event.event_20250612.assets import *


class EventPartError(Exception):
    pass

class EventDifficultyError(Exception):
    pass

class EventPartUnavailableError(Exception):
    pass

class HardEventAvailable(Exception):
    pass

class EventUnavailableError(Exception):
    pass

class NoOpportunityRemain(Exception):
    pass

class ChallengeNotFoundError(Exception):
    pass

class EventInfo:
    def __init__(self, id, name, type):
        self.id: str = id
        self.name: str = name
        self.type: int = type

class StoryEvent(UI):
    @cached_property
    def event(self) -> EventInfo:
        for k, v in self.config.EVENTS[0].items():
            self.config.__setattr__(k, v)
        return EventInfo(*self.config.EVENTS[0].values())

    @Config.when(EVENT_TYPE=1)
    def login_stamp(self, skip_first_screenshot=True):
        logger.hr('START EVENT LOGIN STAMP')
        click_timer = Timer(0.3)
        
        # 进入签到页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() \
                    and self.appear(EVENT_CHECK, offset=10) \
                    and self.appear_then_click(LOGIN_STAMP, offset=10, interval=5):
                click_timer.reset()
                continue

            if self.appear(LOGIN_STAMP_CHECK, offset=10):
                click_timer.reset()
                break

        # 签到
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 返回活动页面
            if self.appear(EVENT_CHECK, offset=10):
                break

            # 返回
            if click_timer.reached() \
                    and self.appear(LOGIN_STAMP_CHECK, offset=10) \
                    and self.appear(LOGIN_STAMP_DONE, offset=10, threshold=0.9) \
                    and self.appear_then_click(GOTO_BACK, offset=10, interval=1):
                click_timer.reset()
                continue

            # 全部领取
            if click_timer.reached() \
                    and self.appear(LOGIN_STAMP_CHECK, offset=10) \
                    and self.appear_then_click(LOGIN_STAMP_REWARD, offset=10, interval=1, threshold=0.9):
                click_timer.reset()
                continue

            # 点击领取
            if click_timer.reached() \
                    and self.appear_then_click(RECEIVE, offset=10, interval=1):
                click_timer.reset()
                continue

            # 点击跳过
            if click_timer.reached() \
                    and self.appear_then_click(SKIP, offset=10, interval=1):
                click_timer.reset()
                continue

        # self.ui_ensure(page_event)
        logger.info('Login stamp done')

    @Config.when(EVENT_TYPE=2)
    def login_stamp(self):
        logger.info('Small event, skip loginstamp')

    @Config.when(EVENT_TYPE=1)
    def challenge(self, skip_first_screenshot=True):
        logger.hr('START EVENT CHALLENGE')
        click_timer = Timer(0.3)
        
        # 进入挑战页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() \
                    and self.appear(EVENT_CHECK, offset=10) \
                    and self.appear_then_click(CHALLENGE, offset=10, interval=5):
                click_timer.reset()
                continue

            if self.appear(CHALLENGE_CHECK, offset=10):
                self.device.sleep(2)
                break

        self.device.screenshot()
        # 判断新挑战关卡
        challenge_stages = TEMPLATE_CHALLENGE_STAGE.match_multi(self.device.image, similarity=0.7, name='CHALLENGE_STAGE')
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
            if self.appear(CHALLENGE_STAGE_CHECK, offset=10) \
                    and self.appear(CHALLENGE_QUICK_DISABLE, threshold=10) \
                    and self.appear(CHALLENGE_BATTLE_DONE, threshold=10) \
                    and self.appear_then_click(CHALLENGE_CANCEL, offset=10, interval=1):
                break

            # 战斗结束
            if click_timer.reached() \
                    and self.appear_then_click(END_FIGHTING, offset=10, interval=1):
                click_timer.reset()
                break

            # 快速战斗
            if click_timer.reached() \
                    and self.appear(CHALLENGE_STAGE_CHECK, offset=10) \
                    and self.appear(CHALLENGE_BATTLE, threshold=10) \
                    and self.appear_then_click(CHALLENGE_QUICK_ENABLE, threshold=10, interval=1):
                click_timer.reset()
                continue

            # 使用票进行战斗
            if click_timer.reached() \
                    and self.appear(CHALLENGE_QUICK_CHECK, threshold=10) \
                    and self.appear_then_click(CHALLENGE_QUICK_TICKET, offset=10, interval=1):
                click_timer.reset()
                continue

            # 进入战斗
            if click_timer.reached() \
                    and self.appear(CHALLENGE_STAGE_CHECK, offset=10) \
                    and self.appear(CHALLENGE_QUICK_DISABLE, threshold=10) \
                    and self.appear_then_click(CHALLENGE_BATTLE, threshold=10, interval=1):
                click_timer.reset()
                continue

            if click_timer.reached() \
                        and self.appear_then_click(AUTO_SHOOT, offset=10, interval=5, threshold=0.8):
                    click_timer.reset()
                    continue

            if click_timer.reached() \
                    and self.appear_then_click(AUTO_BURST, offset=10, interval=5, threshold=0.8):
                click_timer.reset()
                continue

            if self.appear(OPERATION_FAILED, offset=10):
                raise OperationFailed

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 返回活动页面
            if self.appear(EVENT_CHECK, offset=10):
                break

            # 返回
            if click_timer.reached() \
                    and self.appear(CHALLENGE_CHECK, offset=10) \
                    and self.appear_then_click(GOTO_BACK, offset=10, interval=1):
                click_timer.reset()
                continue

        logger.info('Event challenge done')

    @Config.when(EVENT_TYPE=2)
    def challenge(self):
        logger.info('Small event, skip loginstamp')

    @Config.when(EVENT_TYPE=1)
    def reward(self, skip_first_screenshot=True):
        logger.hr('START EVENT REWARD')
        click_timer = Timer(0.3)
        
        # 进入任务页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() \
                    and self.appear(EVENT_CHECK, offset=10) \
                    and self.appear_then_click(REWARD, offset=10, interval=1):
                click_timer.reset()
                continue

            if self.appear(REWARD_CHECK, offset=10):
                break

        # 领取奖励
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 返回活动页面
            if self.appear(EVENT_CHECK, offset=10):
                break

            # 关闭
            if click_timer.reached() \
                    and self.appear(REWARD_CHALLENGE_CHECK, threshold=10) \
                    and self.appear(REWARD_RECEIVE_DONE, threshold=10) \
                    and self.appear_then_click(REWARD_CLOSED, offset=10, interval=1):
                click_timer.reset()
                continue

            # 领取
            if click_timer.reached() \
                    and self.appear_then_click(REWARD_RECEIVE, threshold=10, interval=1):
                click_timer.reset()
                continue

            # 点击领取
            if click_timer.reached() \
                    and self.appear_then_click(RECEIVE, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

            # 进入成就页面
            if click_timer.reached() \
                    and self.appear(REWARD_MISSION_CHECK, threshold=10) \
                    and self.appear(REWARD_MISSION_CLEARED, offset=10) \
                    and self.appear_then_click(REWARD_CHALLENGE_HIDDEN, offset=10, interval=1):
                click_timer.reset()
                continue

            # 进入成就页面
            if click_timer.reached() \
                    and self.appear(REWARD_MISSION_CHECK, threshold=10) \
                    and self.appear(REWARD_RECEIVE_DONE, threshold=10) \
                    and self.appear_then_click(REWARD_CHALLENGE_HIDDEN, offset=10, interval=1):
                click_timer.reset()
                continue

        logger.info('Event reward done')

    @Config.when(EVENT_TYPE=2)
    def reward(self, skip_first_screenshot=True):
        logger.hr('START EVENT REWARD')

    @Config.when(EVENT_TYPE=1)
    def story(self, skip_first_screenshot=True):
        logger.hr('START EVENT STORY')
        click_timer = Timer(0.3)

        logger.info('Finding opened event story')
        story1 = False
        story2 = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 检查story2是否开启，未开启则进入1
            if self.appear(EVENT_GOTO_STORY_1, offset=10) \
                    and self.appear(EVENT_GOTO_STORY_2_LOCKED, offset=10):
                logger.info('Find opened event story 1')

                # 进入story1
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    if click_timer.reached() \
                            and self.appear_then_click(EVENT_GOTO_STORY_1, offset=10, interval=1):
                        click_timer.reset()
                        continue

                    # story1页面
                    if self.appear(STORY_1_CHECK, offset=10):
                        story1 = True
                        break
                logger.info('Opened event story 1')

            # 检查story2是否开启，开启则进入2
            if self.appear(EVENT_GOTO_STORY_1, offset=10) \
                    and not self.appear(EVENT_GOTO_STORY_2_LOCKED, offset=10):
                logger.info('Find opened event story 2')
                if self.config.StoryEvent_StoryPart == "Story_1":
                    raise EventPartError

                # 进入story2，story2更新后需要重新截图
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    if click_timer.reached() \
                            and self.appear_then_click(EVENT_GOTO_STORY_2, offset=10, interval=1):
                        click_timer.reset()
                        continue

                    # story2页面
                    if self.appear(STORY_2_CHECK, offset=10):
                        story2 = True
                        break
                logger.info('Opened event story 2')

            if story1 or story2:
                break

        # TODO 

    @Config.when(EVENT_TYPE=2)
    def story(self, skip_first_screenshot=True):
        logger.hr('START EVENT STORY')

    def run(self):
        try:
            self.ui_ensure(page_main)
            if not self.appear(MAIN_GOTO_EVENT, offset=10):
                raise EventUnavailableError
            self.ui_ensure(page_event)
            _ = self.event
            if self.config.StoryEvent_LoginStamp:
                self.login_stamp()
            if self.config.StoryEvent_Challenge:
                self.challenge()
            if self.config.StoryEvent_Story:
                self.story()

            self.reward()

        except EventPartError as e:
            logger.error(e)
        except EventDifficultyError as e:
            logger.error(e)
        except NoOpportunityRemain as e:
            self.ensure_back()
            logger.warning('There are no opportunities remaining')
        except EventUnavailableError as e:
            logger.error('The event is no longer available')
        except ChallengeNotFoundError as e:
            logger.error('Challenge stage not found')
        except OperationFailed as e:
            logger.error('Challenge stage battle failed')

        self.config.task_delay(server_update=True)
