from module.base.langs import Langs
from module.base.timer import Timer
from module.base.utils import crop
from module.coop.assets import *
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.simulation_room.assets import AUTO_BURST, AUTO_SHOOT, END_FIGHTING
from module.ui.page import page_main
from module.ui.ui import UI


class NoOpportunityRemain(Exception):
    pass


class CoopIsUnavailable(Exception):
    pass


class Coop(UI):
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
    def coop_date(self) -> str:
        model_type = self.config.Optimization_OcrModelType
        DATELINE = Ocr(
            [DATELINE_CHECK.area],
            name='DATELINE',
            model_type=model_type,
            lang='ch',
        )

        return DATELINE.ocr(self.device.image)['text']

    @property
    def dateline(self) -> bool:
        date = self.coop_date
        if date == Langs.COOP_TIMELINE_TIMEOUT or (
            Langs.COOP_TIMELINE_HOUR not in date and Langs.COOP_TIMELINE_LEFT not in date
        ):
            logger.info('[Coop has expired]')
            return True
        return False

    def ensure_into_coop(self, skip_first_screenshot=True):
        """普通协同，从banner进入作战"""
        logger.hr('COOP START')
        coop_enter = False

        self.ensure_sroll((260, 150), (30, 150), method='swipe', speed=30, count=1, delay=0.5)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if not coop_enter:
                scroll_timer = Timer(60, count=30).start()
                # 滑动banner查找协同作战
                self.ensure_sroll((260, 150), (30, 150), method='swipe', speed=30, count=1, delay=0.5)
                self.ensure_sroll((30, 150), (260, 150), method='swipe', speed=30, count=1, delay=0.5)
                self.device.screenshot()
                banner_first = Button(EVENT_BANNER.area, None, button=EVENT_BANNER.area)
                banner_first._match_init = True
                banner_first.image = crop(self.device.image, EVENT_BANNER.area)
                while 1:
                    # 超时检查
                    if scroll_timer.reached():
                        logger.warning('Search coop banner timeout')
                        raise CoopIsUnavailable

                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    tmp_image = self.device.image
                    # 滑动到下一个banner
                    self.ensure_sroll((260, 150), (30, 150), method='swipe', speed=30, count=1, delay=0.5)
                    # 比较banner是否变化
                    while 1:
                        self.device.screenshot()

                        banner = Button(EVENT_BANNER.area, None, button=EVENT_BANNER.area)
                        banner._match_init = True
                        banner.image = crop(tmp_image, EVENT_BANNER.area)
                        if self.appear(banner, offset=10, threshold=0.8):
                            continue
                        else:
                            break

                    # 回到第一个banner
                    if self.appear(banner_first, offset=10, threshold=0.8):
                        if self.appear_then_click(COOP_BANNER_CHECK, offset=10, threshold=0.65, interval=2):
                            logger.info('Find coop in banner')
                            coop_enter = True
                            break
                        else:
                            logger.info('Not find coop in banner')
                            raise CoopIsUnavailable

                    if self.appear_then_click(COOP_BANNER_CHECK, offset=10, threshold=0.65, interval=2):
                        logger.info('Find coop in banner')
                        coop_enter = True
                        break

            if self.appear(COOP_CHECK, offset=10):
                break

        if self.free_opportunity_remain and not self.dateline:
            self.start_coop()
        else:
            logger.info('There are no free opportunities')

    def start_coop(self, skip_first_screenshot=True):
        logger.hr('Start a coop')
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 选择普通难度
            if (
                click_timer.reached()
                and self.appear(SECECT_DIFFICULTY, offset=10)
                and self.appear_then_click(DIFFICULTY_NORMAL_NOT_CHECKED, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 确认难度
            if (
                click_timer.reached()
                and self.appear(SECECT_DIFFICULTY, offset=10)
                and self.appear(DIFFICULTY_NORMAL, offset=10)
                and self.appear_then_click(DIFFICULTY_CONFIRM, offset=10, interval=1)
            ):
                click_timer.reset()
                continue

            # 协同开始
            if (
                click_timer.reached()
                and self.appear(COOP_ROLE_CHECK, offset=10)
                and not self.appear(COOP_CANCEL, offset=10)
                and self.appear_then_click(COOP_START, offset=10, interval=10, threshold=0.3)
            ):
                self.device.sleep(1)
                click_timer.reset()
                continue

            # 接受
            if click_timer.reached() and self.appear_then_click(COOP_ACCEPT, offset=30, interval=1):
                click_timer.reset()
                continue

            # TODO 选择妮姬
            # if click_timer.reached() \
            #         and self.appear_then_click(COOP_START, offset=10, interval=1):
            #     click_timer.reset()
            #     continue

            # 准备
            if click_timer.reached() and self.appear_then_click(COOP_PREPARE, offset=10, interval=1):
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
                break

        # 进入协同作战界面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(COOP_CHECK, offset=10):
                break

        if self.free_opportunity_remain:
            self.device.click_record_clear()
            self.device.stuck_record_clear()
            return self.start_coop()
        else:
            logger.info('There are no free opportunities')
            raise NoOpportunityRemain

    def run(self):
        try:
            self.ui_ensure(page_main)
            self.ensure_into_coop()
        except CoopIsUnavailable:
            pass
        except NoOpportunityRemain:
            pass

        self.config.task_delay(server_update=True)
