from module.base.decorator import Config
from module.base.timer import Timer
from module.event.assets import *
from module.event.base import EventBase
from module.event.challenge import CHALLENGE_QUICKLY_DISABLE
from module.logger import logger
from module.simulation_room.assets import END_FIGHTING, FIGHT_QUICKLY
from module.ui.assets import FIGHT_CLOSE, FIGHT_QUICKLY_CHECK, FIGHT_QUICKLY_FIGHT, FIGHT_QUICKLY_MAX, FIGHT_QUICKLY_MIN


class EventStorySweep(EventBase):
    def STORY_STAGE_11(self, story):
        stages = {
            'story_1_normal': self.event_assets.STORY_1_NORMAL_STAGE_11,
            'story_1_normal_clear': self.event_assets.STORY_1_NORMAL_STAGE_11_CLEAR,
            'story_1_hard': self.event_assets.STORY_1_HARD_STAGE_11,
            'story_1_hard_clear': self.event_assets.STORY_1_HARD_STAGE_11_CLEAR,
            'story_2_normal': self.event_assets.STORY_2_NORMAL_STAGE_11,
            'story_2_normal_clear': self.event_assets.STORY_2_NORMAL_STAGE_11_CLEAR,
            'story_2_hard': self.event_assets.STORY_2_HARD_STAGE_11,
            'story_2_hard_clear': self.event_assets.STORY_2_HARD_STAGE_11_CLEAR,
        }
        return stages[story]

    @Config.when(EVENT_TYPE=(1, 3))
    def story_sweep(self, skip_first_screenshot=True):
        logger.hr('START EVENT STORY', 2)
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
                    if not self.appear(self.event_assets.EVENT_GOTO_STORY_1, offset=10) and self.appear(
                        self.event_assets.STORY_1_NORMAL, threshold=10
                    ):
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
                    if not self.appear(self.event_assets.EVENT_GOTO_STORY_2, offset=10) and self.appear(
                        self.event_assets.STORY_2_NORMAL, threshold=10
                    ):
                        click_timer.reset()
                        break

                    # story2困难难度列表页面
                    if not self.appear(self.event_assets.EVENT_GOTO_STORY_2, offset=10) and self.appear(
                        self.event_assets.STORY_2_HARD, threshold=10
                    ):
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
        self.ensure_sroll_to_bottom(x1=(680, 800), x2=(680, 460), count=3)
        self.device.screenshot()
        self.find_and_fight_stage(open_story)

        # 回到活动主页
        self.back_to_event()

    @Config.when(EVENT_TYPE=2)
    def story_sweep(self, skip_first_screenshot=True):
        logger.hr('START EVENT STORY', 2)
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
                if not self.appear_then_click(self.event_assets.STORY_1_CHECK, offset=10) and self.appear(
                    self.event_assets.STORY_1_NORMAL, threshold=10
                ):
                    click_timer.reset()
                    break

                # story困难难度列表页面，困难更新后需要重新截图
                if not self.appear_then_click(self.event_assets.STORY_1_CHECK, offset=10) and self.appear(
                    self.event_assets.STORY_1_HARD, threshold=10
                ):
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
        self.ensure_sroll_to_bottom(x1=(680, 800), x2=(680, 460), count=3)
        self.device.screenshot()
        self.find_and_fight_stage(open_story)

        # 回到活动主页
        self.back_to_event()

    def find_and_fight_stage(self, open_story):
        click_timer = Timer(0.3)
        if self.appear(self.STORY_STAGE_11(open_story), offset=30, threshold=0.9) and self.appear(
            self.STORY_STAGE_11(f'{open_story}_clear'), offset=30, threshold=0.9
        ):
            max_clicks = 0
            while 1:
                self.device.screenshot()

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

                # 关卡检查
                if (
                    click_timer.reached()
                    and self.appear(self.STORY_STAGE_11(open_story), offset=30, threshold=0.9)
                    and self.appear_then_click(self.STORY_STAGE_11(f'{open_story}_clear'), offset=30, threshold=0.9)
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
