from module.config.delay import next_tuesday
from module.handler.assets import REWARD
from module.logger import logger
from module.outpost.assets import *
from module.ui.assets import SKIP
from module.ui.page import page_outpost
from module.ui.ui import UI


class NoStoryRemaining(Exception):
    pass


class NoTimesRemaining(Exception):
    pass


class OutpostStory(UI):
    def check_remaining(self):
        # 检查次数
        while 1:
            self.device.screenshot()

            # 剧情图标
            if self.appear(STORY_OPEN, offset=10):
                # 所有剧情已看完
                if self.appear(STORY_REMAINING_0, offset=10, threshold=0.9):
                    raise NoStoryRemaining
                # 次数不够
                if self.appear(STORY_TIMES_0, offset=10, threshold=0.9):
                    raise NoTimesRemaining
                break
            else:
                continue

    def story_view(self):
        logger.hr('Story view', 2)
        self.check_remaining()

        skip = False
        while 1:
            self.device.screenshot()

            if (
                not self.appear(STORY_DIALOG_BOX, offset=100)
                and not self.appear(STORY_CLOSE, offset=10)
                and self.appear_then_click(STORY_OPEN, offset=10, interval=1)
            ):
                logger.info('Open story list')
                continue

            # 进入剧情
            if (
                not skip
                and not self.appear(STORY_START_CONFIRM, offset=10)
                and self.appear_then_click(STORY_START, offset=10, interval=3)
            ):
                continue

            # 进入剧情确认
            if self.appear(STORY_START_CHECK, offset=10) and self.appear_then_click(
                STORY_START_CONFIRM, offset=10, interval=1
            ):
                continue

            # 任意对话框
            if not skip and self.appear_then_click(STORY_DIALOG_BOX, offset=100, click_offset=(30, -15), interval=3):
                continue

            # SKIP
            if self.appear_then_click(SKIP, offset=10, interval=1):
                skip = True
                continue

            # 关闭，回到基地主页
            if skip and self.appear_then_click(STORY_CLOSE, offset=10, interval=1):
                while 1:
                    self.device.screenshot()
                    if not self.appear(STORY_CLOSE, offset=10):
                        logger.info('Story view done')
                        self.device.click_record_clear()
                        self.device.stuck_record_clear()
                        skip = False
                        return self.story_view()
                    if self.appear_then_click(STORY_CLOSE, offset=10, interval=1):
                        continue
                continue

            # 领取
            if self.appear_then_click(REWARD, offset=10, interval=1, static=False):
                continue

    def run(self):
        logger.hr('Outpost Story')
        self.ui_ensure(page_outpost)

        try:
            self.story_view()
        except NoStoryRemaining:
            logger.warning('All story allready viewed')
        except NoTimesRemaining:
            logger.warning('No times remaining for story')

        self.config.task_delay(target=next_tuesday())
