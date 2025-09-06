import importlib
from functools import cached_property

from module.base.button import filter_buttons_in_area
from module.base.decorator import Config
from module.base.timer import Timer
from module.base.utils import get_button_by_location, sort_buttons_by_location
from module.challenge.assets import *
from module.coop.assets import *
from module.coop.coop import Coop, CoopIsUnavailable
from module.event.assets import *
from module.exception import (
    RequestHumanTakeover,
)
from module.logger import logger
from module.simulation_room.assets import (
    AUTO_BURST,
    AUTO_SHOOT,
    END_FIGHTING,
    FIGHT_QUICKLY,
)
from module.tribe_tower.assets import OPERATION_FAILED
from module.ui.assets import (
    EVENT_SWITCH,
    FIGHT_CLOSE,
    FIGHT_QUICKLY_CHECK,
    FIGHT_QUICKLY_FIGHT,
    FIGHT_QUICKLY_MAX,
    GOTO_BACK,
    MAIN_CHECK,
)
from module.ui.page import *
from module.ui.ui import UI

from .minigame.game import game


class EventSelectError(Exception):
    pass


class EventUnavailableError(Exception):
    pass


class ChallengeNotFoundError(Exception):
    pass


class EventInfo:
    def __init__(self, id, name, type, mini_game, story_part, story_difficulty):
        self.id: str = id
        self.name: str = name
        self.type: int = type
        self.mini_game: bool = mini_game
        self.story_part: str = story_part
        self.story_difficulty: str = story_difficulty


class Event(UI):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.event_assets = self.load_event_assets('assets')
        if self.event.mini_game:
            self.minigame_assets = self.load_event_assets('assets_game')

    def load_event_assets(self, module_name):
        """动态加载资源模块"""
        event_id = self.event.id
        try:
            module_name = f'module.event.{event_id}.{module_name}'
            return importlib.import_module(module_name)
        except ImportError:
            logger.warning(f'Assets module not found: {module_name}')
            raise EventUnavailableError

    def STORY_STAGE_11(self, story):
        stages = {
            'story_1_normal': self.event_assets.STORY_1_NORMAL_STAGE_11,
            'story_1_hard': self.event_assets.STORY_1_HARD_STAGE_11,
            'story_2_normal': self.event_assets.STORY_2_NORMAL_STAGE_11,
            'story_2_hard': self.event_assets.STORY_2_HARD_STAGE_11,
        }
        return stages[story]

    @cached_property
    def event(self) -> EventInfo:
        target_event_id = getattr(self.config, 'Event_Event', None)

        event_config = next(
            (e for e in self.config.EVENTS if e.get('event_id') == target_event_id), self.config.EVENTS[0]
        )

        for k, v in event_config.items():
            self.config.__setattr__(k, v)

        return EventInfo(*event_config.values())

    def back_to_event(self):
        logger.info('Back to event')
        click_timer = Timer(0.3)

        # 回到活动主页
        while 1:
            self.device.screenshot()

            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            if click_timer.reached() and self.appear_then_click(GOTO_BACK, offset=10, interval=2):
                click_timer.reset()
                continue

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

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.LOGIN_STAMP, offset=10, interval=5)
            ):
                click_timer.reset()
                continue

            if self.appear(self.event_assets.LOGIN_STAMP_CHECK, offset=10):
                click_timer.reset()
                break

        # 签到
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 返回活动页面
            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            # 返回
            if (
                click_timer.reached()
                and self.appear(self.event_assets.LOGIN_STAMP_CHECK, offset=10)
                and self.appear(self.event_assets.LOGIN_STAMP_DONE, threshold=10)
                and self.appear_then_click(GOTO_BACK, offset=(30, 30), interval=2)
            ):
                click_timer.reset()
                continue

            # 全部领取
            if (
                click_timer.reached()
                and self.appear(self.event_assets.LOGIN_STAMP_CHECK, offset=10)
                and self.appear_then_click(self.event_assets.LOGIN_STAMP_REWARD, threshold=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 点击领取
            if click_timer.reached() and self.appear_then_click(RECEIVE, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

            # 点击跳过
            if click_timer.reached() and self.appear_then_click(self.event_assets.SKIP, offset=10, interval=1):
                click_timer.reset()
                continue

        # self.ui_ensure(page_event)
        logger.info('Login stamp done')

    @Config.when(EVENT_TYPE=(2, 3))
    def login_stamp(self):
        logger.hr('START EVENT LOGIN STAMP')
        logger.info('Small event, skip loginstamp')

    def challenge(self, skip_first_screenshot=True):
        logger.hr('START EVENT CHALLENGE')
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
            if click_timer.reached() and self.appear(END_FIGHTING, offset=10):
                while 1:
                    self.device.screenshot()
                    if not self.appear(END_FIGHTING, offset=10):
                        click_timer.reset()
                        break
                    if self.appear_then_click(END_FIGHTING, offset=10, interval=1):
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

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 返回活动页面
            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            # 返回
            if (
                click_timer.reached()
                and self.appear(CHALLENGE_CHECK, offset=10)
                and self.appear_then_click(GOTO_BACK, offset=10, interval=2)
            ):
                click_timer.reset()
                continue

        logger.info('Event challenge done')

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

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.REWARD, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            if self.appear(self.event_assets.REWARD_CHECK, offset=10):
                self.device.sleep(1)
                break

        # 领取奖励
        while 1:
            self.device.screenshot()

            # 返回活动页面
            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            # 关闭
            if (
                click_timer.reached()
                and self.appear(self.event_assets.REWARD_CHALLENGE_CHECK, threshold=5)
                and self.appear(self.event_assets.REWARD_RECEIVE_DONE, threshold=5)
                and self.appear_then_click(self.event_assets.REWARD_CLOSED, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 领取
            if click_timer.reached() and self.appear_then_click(
                self.event_assets.REWARD_RECEIVE, threshold=10, interval=1
            ):
                click_timer.reset()
                continue

            # 点击领取
            if click_timer.reached() and self.appear_then_click(RECEIVE, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

            # 点击reward领取
            if click_timer.reached() and self.appear_then_click(RECEIVE_REWARD, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

            # 进入成就页面
            if (
                click_timer.reached()
                and self.appear(self.event_assets.REWARD_MISSION_CHECK, threshold=5)
                and self.appear(self.event_assets.REWARD_MISSION_CLEARED, offset=10)
                and self.appear_then_click(
                    self.event_assets.REWARD_CHALLENGE_HIDDEN, offset=10, threshold=0.95, interval=1
                )
            ):
                click_timer.reset()
                continue

            # 进入成就页面
            if (
                click_timer.reached()
                and self.appear(self.event_assets.REWARD_MISSION_CHECK, threshold=5)
                and self.appear(self.event_assets.REWARD_RECEIVE_DONE, threshold=5)
                and self.appear_then_click(
                    self.event_assets.REWARD_CHALLENGE_HIDDEN, offset=10, threshold=0.95, interval=1
                )
            ):
                click_timer.reset()
                continue

        logger.info('Event reward done')

    @Config.when(EVENT_TYPE=(2, 3))
    def reward(self, skip_first_screenshot=True):
        logger.hr('START EVENT REWARD')
        click_timer = Timer(0.3)

        # 进入任务页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.REWARD, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            if self.appear(self.event_assets.REWARD_CHECK, offset=10):
                break

        # 领取奖励
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 返回活动页面
            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            # 关闭
            if (
                click_timer.reached()
                and self.appear(self.event_assets.REWARD_RECEIVE_DONE, threshold=10)
                and self.appear_then_click(self.event_assets.REWARD_CLOSED, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 领取
            if click_timer.reached() and self.appear_then_click(
                self.event_assets.REWARD_RECEIVE, threshold=10, interval=1
            ):
                click_timer.reset()
                continue

            # 点击领取
            if click_timer.reached() and self.appear_then_click(RECEIVE, offset=10, interval=1, static=False):
                click_timer.reset()
                continue

        logger.info('Event reward done')

    @Config.when(EVENT_TYPE=(1, 3))
    def story(self, skip_first_screenshot=True):
        logger.hr('START EVENT STORY')
        click_timer = Timer(0.3)

        logger.info('Finding opened event story')
        open_story = 'story_1_normal'
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 检查story2是否开启，未开启则进入1
            if self.appear(self.event_assets.EVENT_GOTO_STORY_1, offset=10) and self.appear(
                self.event_assets.EVENT_GOTO_STORY_2_LOCKED, offset=10
            ):
                logger.info('Find opened event story 1')
                if self.config.Event_StoryPart == 'Story_2':
                    logger.error('The event stage/difficulty select wrong')
                    self.back_to_event()
                    return

                # 进入story1
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.EVENT_GOTO_STORY_1, offset=10, interval=5
                    ):
                        click_timer.reset()
                        continue

                    # story1主页
                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.STORY_1_CHECK, offset=10, interval=3
                    ):
                        click_timer.reset()
                        continue

                    # story1列表页面
                    if self.appear(self.event_assets.STORY_1_NORMAL, threshold=10):
                        click_timer.reset()
                        break
                logger.info('Open event story 1')
                break

            # 检查story2是否开启，开启则进入2
            if (
                self.appear(self.event_assets.EVENT_GOTO_STORY_1, offset=10)
                and not self.appear(self.event_assets.EVENT_GOTO_STORY_2_LOCKED, offset=10)
            ) or self.appear(self.event_assets.EVENT_GOTO_STORY_2, offset=10):
                logger.info('Find opened event story 2')
                if self.config.Event_StoryPart == 'Story_1':
                    logger.error('The event stage/difficulty select wrong')
                    self.back_to_event()
                    return

                # 进入story2，story2更新后需要重新截图
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.EVENT_GOTO_STORY_2, offset=10, interval=5
                    ):
                        click_timer.reset()
                        continue

                    # story2主页
                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.STORY_2_CHECK, offset=10, interval=3
                    ):
                        click_timer.reset()
                        continue

                    # story2困难解锁
                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.STORY_2_HARD_UNLOCK, offset=10, interval=1
                    ):
                        open_story = 'story_2_hard'
                        click_timer.reset()
                        continue

                    # story2普通难度列表页面
                    if self.appear(self.event_assets.STORY_2_NORMAL, threshold=10):
                        click_timer.reset()
                        break

                    # story2困难难度列表页面
                    if self.appear(self.event_assets.STORY_2_HARD, threshold=10):
                        click_timer.reset()
                        break
                self.device.sleep(2)
                logger.info('Open event story 2')

                self.device.screenshot()
                # 困难难度关闭
                if self.appear(self.event_assets.STORY_2_NORMAL, threshold=10) and self.appear(
                    self.event_assets.STORY_2_HARD_LOCKED, offset=10
                ):
                    logger.info('Find difficulty normal opened')
                    if self.config.Event_StoryDifficulty == 'Hard':
                        logger.error('The event stage/difficulty select wrong')
                        self.back_to_event()
                        return
                    open_story = 'story_2_normal'
                    logger.info('Open event story 2 normal')
                    break

                # 困难难度开启，当前页面是普通
                if self.appear(self.event_assets.STORY_2_NORMAL, threshold=10) and not self.appear(
                    self.event_assets.STORY_2_HARD_LOCKED, offset=10
                ):
                    open_story = 'story_2_hard'

                # 困难难度开启，当前页面是困难
                if self.appear(self.event_assets.STORY_2_HARD, threshold=10):
                    open_story = 'story_2_hard'

                if open_story == 'story_2_hard':
                    logger.info('Find difficulty hard opened')
                    if self.config.Event_StoryDifficulty == 'Normal':
                        logger.error('The event stage/difficulty select wrong')
                        self.back_to_event()
                        return

                    while 1:
                        if skip_first_screenshot:
                            skip_first_screenshot = False
                        else:
                            self.device.screenshot()

                        # story2困难难度切换
                        if click_timer.reached() and self.appear_then_click(
                            self.event_assets.STORY_2_HARD_HIDDEN, threshold=10
                        ):
                            click_timer.reset()
                            continue

                        # story2困难难度列表页面
                        if self.appear(self.event_assets.STORY_2_HARD, threshold=10):
                            click_timer.reset()
                            break

                    logger.info('Open event story 2 hard')
                    break

        # 滑动到列表最下方检查倒数第二关
        self.ensure_sroll_to_bottom(x1=(680, 900), x2=(680, 460), count=3)
        self.device.screenshot()
        self.find_and_fight_stage(open_story)

        # 回到活动主页
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            if click_timer.reached() and self.appear_then_click(GOTO_BACK, offset=10, interval=2):
                click_timer.reset()
                continue

    @Config.when(EVENT_TYPE=2)
    def story(self, skip_first_screenshot=True):
        logger.hr('START EVENT STORY')
        click_timer = Timer(0.3)

        open_story = 'story_1_normal'
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 进入关卡列表
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                # story主页
                if click_timer.reached() and self.appear_then_click(
                    self.event_assets.STORY_1_CHECK, offset=10, interval=3
                ):
                    click_timer.reset()
                    continue

                # story困难解锁，困难更新后需要重新截图
                if click_timer.reached() and self.appear_then_click(
                    self.event_assets.STORY_1_HARD_UNLOCK, offset=10, interval=1
                ):
                    click_timer.reset()
                    continue

                # story普通难度列表页面
                if self.appear(self.event_assets.STORY_1_NORMAL, threshold=10):
                    click_timer.reset()
                    break

                # story困难难度列表页面，困难更新后需要重新截图
                if self.appear(self.event_assets.STORY_1_HARD, threshold=10):
                    click_timer.reset()
                    break

            # 困难难度关闭
            if self.appear(self.event_assets.STORY_1_NORMAL, threshold=10) and self.appear(
                self.event_assets.STORY_1_HARD_LOCKED, offset=10
            ):
                logger.info('Find difficulty normal opened')
                if self.config.Event_StoryDifficulty == 'Hard':
                    logger.error('The event stage/difficulty select wrong')
                    self.back_to_event()
                    return
                open_story = 'story_1_normal'
                logger.info('Open event story normal')
                break

            # 困难难度开启，当前页面是普通
            if self.appear(self.event_assets.STORY_1_NORMAL, threshold=10) and not self.appear(
                self.event_assets.STORY_1_HARD_LOCKED, offset=10
            ):
                open_story = 'story_1_hard'

            # 困难难度开启，当前页面是困难
            if self.appear(self.event_assets.STORY_1_HARD, threshold=10):
                open_story = 'story_1_hard'

            if open_story == 'story_1_hard':
                logger.info('Find difficulty hard opened')
                if self.config.Event_StoryDifficulty == 'Normal':
                    logger.error('The event stage/difficulty select wrong')
                    self.back_to_event()
                    return

                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    # story困难难度切换
                    if click_timer.reached() and self.appear_then_click(
                        self.event_assets.STORY_1_HARD_HIDDEN, threshold=10
                    ):
                        click_timer.reset()
                        continue

                    # story困难难度列表页面
                    if self.appear(self.event_assets.STORY_1_HARD, threshold=10):
                        click_timer.reset()
                        break

                logger.info('Open event story hard')
                break

        # 滑动到列表最下方检查倒数第二关
        self.ensure_sroll_to_bottom(x1=(680, 900), x2=(680, 460), count=3)
        self.device.screenshot()
        self.find_and_fight_stage(open_story)

        # 回到活动主页
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(self.event_assets.STORY_1_CHECK, offset=10):
                break

            if click_timer.reached() and self.appear_then_click(GOTO_BACK, offset=10, interval=2):
                click_timer.reset()
                continue

    def find_and_fight_stage(self, open_story):
        click_timer = Timer(0.3)
        if self.appear(self.STORY_STAGE_11(open_story), offset=10, static=False):
            max_clicks = 0
            while 1:
                self.device.screenshot()

                # 战斗结束
                if click_timer.reached() and self.appear(END_FIGHTING, offset=10):
                    while 1:
                        self.device.screenshot()
                        if not self.appear(END_FIGHTING, offset=10):
                            click_timer.reset()
                            break
                        if self.appear_then_click(END_FIGHTING, offset=10, interval=1):
                            click_timer.reset()
                            continue
                    break

                # 关卡检查
                if click_timer.reached() and self.appear_then_click(
                    self.STORY_STAGE_11(open_story), offset=10, threshold=0.9, interval=1, static=False
                ):
                    self.device.sleep(0.5)
                    click_timer.reset()
                    continue

                # 快速战斗
                if (
                    click_timer.reached()
                    and self.appear(self.event_assets.STORY_STAGE_CHECK, offset=30)
                    and self.appear_then_click(FIGHT_QUICKLY, threshold=20, interval=1)
                ):
                    click_timer.reset()
                    continue

                # 票max
                if (
                    click_timer.reached()
                    and max_clicks < 3
                    and self.appear(FIGHT_QUICKLY_CHECK, offset=10)
                    and self.appear_then_click(FIGHT_QUICKLY_MAX, offset=30, threshold=0.99, interval=1)
                ):
                    max_clicks += 1
                    self.device.sleep(0.3)
                    click_timer.reset()
                    continue

                # 进行战斗
                if (
                    click_timer.reached()
                    and self.appear(FIGHT_QUICKLY_CHECK, offset=10)
                    and self.appear(FIGHT_QUICKLY_MIN, offset=30, threshold=0.99)
                    and self.appear_then_click(FIGHT_QUICKLY_FIGHT, threshold=20, interval=1)
                ):
                    click_timer.reset()
                    continue

                # 没票
                if (
                    click_timer.reached()
                    and self.appear(self.event_assets.STORY_STAGE_CHECK, offset=10)
                    and self.appear(CHALLENGE_QUICKLY_DISABLE, threshold=10)
                    and self.appear_then_click(FIGHT_CLOSE, offset=10, interval=1)
                ):
                    break
        else:
            logger.info('Stage 11 not cleared')
            return
        logger.info('Stage 11 clear done')

    @Config.when(EVENT_TYPE=1)
    def coop(self, skip_first_screenshot=True):
        """进入协同作战页面"""
        logger.hr('EVENT COOP START')
        click_timer = Timer(0.3)
        confirm_timer = Timer(5, count=3).start()

        # 走到协同作战
        direct = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.COOP_ENTER, offset=10, interval=5)
            ):
                click_timer.reset()
                continue

            if confirm_timer.reached():
                logger.warning('Coop is not enabled')
                return

            # 协同未在开启时间
            if click_timer.reached() and self.appear(self.event_assets.COOP_LOCK, offset=10):
                logger.warning('Coop is not enabled')
                return

            # 协同选择
            if self.appear(self.event_assets.COOP_SELECT_CHECK, offset=10):
                break

            # 协同主页
            if self.appear(COOP_CHECK, offset=10):
                direct = True
                break

        # 检查是否有开启的协同
        coops = self.event_assets.TEMPLATE_COOP_ENABLE.match_multi(self.device.image, name='COOP_ENABLE')
        if not coops and not direct:
            logger.warning('Not find coop in event')
            return

        # 进入协同作战界面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 选择协同
            if click_timer.reached() and self.appear(self.event_assets.COOP_SELECT_CHECK, offset=10, interval=5):
                self.device.click(coops[0])
                click_timer.reset()
                continue

            # 协同主页
            if self.appear(COOP_CHECK, offset=10):
                break

        if self.free_opportunity_remain and not self.dateline:
            _coop = Coop(self.config, self.device)
            _coop.start_coop()
        else:
            logger.info('There are no free opportunities')

        # 回到活动主页
        while 1:
            self.device.screenshot()

            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            if click_timer.reached() and self.appear_then_click(GOTO_BACK, offset=10, interval=2):
                click_timer.reset()
                continue

    @Config.when(EVENT_TYPE=(2, 3))
    def coop(self):
        logger.hr('EVENT COOP START')
        logger.info('Small event, skip coop')

    @cached_property
    def shop_delay_list(self) -> list[str]:
        """
        商店延迟购买列表
        """
        return [line.strip() for line in self.config.Event_ShopDelayList.split('\n') if line.strip()]

    def get_shop_item_button(self, item: str):
        """
        根据选项名称获取对应的按钮
        示例：
          "TITLE" → SHOP_ITEM_TITLE
        """
        button_name = f'SHOP_ITEM_{item}'
        try:
            return globals()[button_name]
        except KeyError:
            logger.error(f"Button asset '{button_name}' not found for option '{item}'")
            raise

    def shop(self, skip_first_screenshot=True):
        logger.hr('START EVENT SHOP')
        click_timer = Timer(0.3)
        restart_flag = False
        delay_list = self.shop_delay_list

        # 进入商店页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.event_assets.SHOP, offset=10, interval=5)
            ):
                click_timer.reset()
                continue

            # 商店页面
            if self.appear(EVENT_SHOP_CHECK, offset=(30, 30)) and self.appear(
                self.event_assets.SHOP_ITEM_LOAD_CHECK, offset=(30, 30)
            ):
                logger.info('Open event shop')
                break

        # 跳过第一个物品
        skip_item_first = False
        while 1:
            # 滑动到商店最上方
            if restart_flag:
                logger.info('Scroll to shop top')
                self.ensure_sroll((360, 600), (360, 900), speed=30, count=5, delay=2)
                restart_flag = False

            self.device.screenshot()
            # 当前页所有商品
            items = self.event_assets.TEMPLATE_SHOP_MONEY.match_multi(
                self.device.image, similarity=0.65, name='SHOP_ITEM'
            )
            # 过滤掉非商店区域的商品
            items = filter_buttons_in_area(items, y_range=(620, 1280))
            # 按照坐标排序
            items = sort_buttons_by_location(items)
            logger.info(f'Find items: {len(items)}')

            # SOLD_OUT的商品
            sold_outs = TEMPLATE_SOLD_OUT.match_multi(self.device.image, similarity=0.7, name='SOLD_OUT')
            logger.info(f'Find slod out items: {len(sold_outs)}')
            # 过滤掉所有SOLD_OUT的商品
            items = self.filter_sold_out_items(items, sold_outs)
            # 如果第一个物品为要推迟购买的物品，在购买列表中删除
            if skip_item_first and items:
                items = items[1:]
                skip_item_first = False

            logger.info(f'Find vaild items: {len(items)}')
            if items:
                while 1:
                    self.device.screenshot()

                    # 购买第一个商品
                    if click_timer.reached() and self.appear(EVENT_SHOP_CHECK, offset=(30, 30)):
                        self.device.click(items[0])
                        click_timer.reset()
                        continue

                    # 商品弹窗
                    if self.appear(SHOP_ITEM_CHECK, offset=10):
                        click_timer.reset()
                        break

                quit = False
                confirm_timer = Timer(1, count=3)
                while 1:
                    self.device.screenshot()

                    # 退出
                    if self.appear(EVENT_SHOP_CHECK, offset=(30, 30)):
                        if not confirm_timer.started():
                            confirm_timer.start()
                        if confirm_timer.reached():
                            if quit:
                                logger.info('Money not enough, quiting')
                                return
                            else:
                                logger.info('Item purchase completed, goto next')
                                break
                    else:
                        confirm_timer.clear()

                    # 商品在延迟购买列表中，跳过，返回商店主页
                    for i, item in enumerate(delay_list[:]):
                        if self.appear(SHOP_ITEM_CHECK, offset=10) and self.appear(
                            self.get_shop_item_button(item), offset=10
                        ):
                            logger.info(f'Skip item purchase: {item}')
                            skip_item_first = True
                            # 取消购买弹窗
                            if click_timer.reached() and self.appear_then_click(SHOP_CANCEL, offset=30, interval=1):
                                click_timer.reset()
                                continue

                    # 商品是红球并且称号没买，重新进入商店
                    if (
                        delay_list
                        and self.appear(SHOP_ITEM_CHECK, offset=10)
                        and self.appear(SHOP_ITEM_RED_CIRCLE, offset=10)
                    ):
                        logger.info('Delaylist not empty, restart shop to purchase')
                        delay_list.clear()
                        restart_flag = True
                        # 取消购买弹窗
                        if click_timer.reached() and self.appear_then_click(SHOP_CANCEL, offset=30, interval=1):
                            click_timer.reset()
                            continue

                    # 取消
                    if (
                        (quit or restart_flag)
                        and click_timer.reached()
                        and self.appear_then_click(SHOP_CANCEL, offset=30, interval=1)
                    ):
                        click_timer.reset()
                        continue

                    # 资金不足
                    if self.appear(SHOP_MONEY_LACK, offset=30):
                        quit = True
                        continue

                    # 点击max
                    if click_timer.reached() and self.appear_then_click(
                        SHOP_BUY_MAX, offset=30, threshold=0.99, interval=1
                    ):
                        click_timer.reset()
                        continue

                    # 购买
                    if click_timer.reached() and self.appear_then_click(SHOP_BUY, offset=30, interval=1):
                        click_timer.reset()
                        continue

                    # 点击领取
                    if click_timer.reached() and self.appear_then_click(RECEIVE, offset=30, interval=1, static=False):
                        self.device.sleep(0.5)
                        click_timer.reset()
                        continue
            else:
                # 当前页全部购买完成，滚动到下一页
                logger.info('Scroll to next page')
                self.device.stuck_record_clear()
                self.device.click_record_clear()
                self.ensure_sroll((360, 1100), (360, 480), speed=5, hold=1, count=1, delay=3, method='scroll')

    def filter_sold_out_items(self, items, sold_outs):
        """
        根据售完标记的位置过滤商品列表
        """
        filtered_items = items.copy()

        for sold_out in sold_outs:
            so_x, so_y = sold_out.location
            to_remove = []

            for item in filtered_items:
                item_x, item_y = item.location

                x_in_range = so_x - 30 <= item_x <= so_x + 30
                y_in_range = so_y <= item_y <= so_y + 120

                if x_in_range and y_in_range:
                    to_remove.append(item)

            for item in to_remove:
                filtered_items.remove(item)

        return filtered_items

    @Config.when(EVENT_MINI_GAME=True)
    def game(self, skip_first_screenshot=True):
        logger.hr('START EVENT GAME')
        click_timer = Timer(0.3)

        # 进入小游戏页面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if (
                click_timer.reached()
                and self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30))
                and self.appear_then_click(self.minigame_assets.MINI_GAME, offset=10, interval=5)
            ):
                click_timer.reset()
                continue

            if click_timer.reached() and self.appear_then_click(
                self.minigame_assets.MINI_GAME_TOUCH, offset=10, interval=2
            ):
                click_timer.reset()
                continue

            if self.appear(self.minigame_assets.MINI_GAME_CHECK, offset=10):
                break

        return game(self)

    @Config.when(EVENT_MINI_GAME=False)
    def game(self):
        logger.hr('START EVENT GAME')
        logger.info('Game not support in this event')

    def ensure_into_event(self, skip_first_screenshot=True):
        logger.hr('OPEN EVENT STORY')
        click_timer = Timer(0.3)
        confirm_timer = Timer(30, count=20).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(self.event_assets.EVENT_CHECK, offset=(30, 30)):
                break

            if (
                click_timer.reached()
                and self.appear(MAIN_CHECK, offset=10)
                and self.appear_then_click(EVENT_SWITCH, offset=10, interval=3)
            ):
                click_timer.reset()
                confirm_timer.reset()
                continue

            if (
                click_timer.reached()
                and self.appear(MAIN_CHECK, offset=10)
                and self.appear_then_click(self.event_assets.MAIN_GOTO_EVENT, offset=(50, 50), interval=5)
            ):
                click_timer.reset()
                logger.info('Open event story')
                continue

            if confirm_timer.reached():
                logger.error('Event not found')
                raise EventUnavailableError

    def run(self):
        try:
            self.ui_ensure(page_main)
            _ = self.event

            self.ensure_into_event()
            if self.config.Event_LoginStamp:
                self.login_stamp()
            if self.config.Event_Challenge:
                self.challenge()
            if self.config.Event_Story:
                self.story()
            if self.config.Event_Coop:
                self.coop()
            if self.config.Event_Game:
                self.game()

            self.reward()

            if self.config.Event_Shop:
                self.shop()

        except EventSelectError:
            logger.error('The event stage/difficulty select wrong')
        except EventUnavailableError:
            logger.error('The event is no longer available')
        except ChallengeNotFoundError:
            logger.error('Challenge stage not found')
        except CoopIsUnavailable:
            pass

        self.config.task_delay(server_update=True)
