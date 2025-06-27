from module.base.decorator import Config
from module.base.timer import Timer
from module.base.utils import crop
from module.logger import logger
from module.simulation_room.assets import AUTO_SHOOT, AUTO_BURST, END_FIGHTING
from module.coop.assets import *
from module.ui.page import page_main, page_event
from module.ui.ui import UI
from module.ocr.ocr import Digit
# 活动引用
from module.event.event_20250612.assets import EVENT_CHECK, COOP_ENTER, \
    COOP_SELECT_CHECK, TEMPLATE_COOP_ENABLE, COOP_LOCK

class NoOpportunityRemain(Exception):
    pass

class CoopIsUnavailable(Exception):
    pass

class Coop(UI):
    @property
    def free_remain(self) -> int:
        FREE_REMAIN = Digit(
            [FREE_OPPORTUNITY_CHECK.area],
            name="FREE_REMAIN",
            letter=(247, 247, 247),
            threshold=128,
            lang="cnocr_num",
        )
        return int(FREE_REMAIN.ocr(self.device.image))
    
    @property
    def free_opportunity_remain(self) -> bool:
        # result = self.appear(FREE_OPPORTUNITY_CHECK, offset=10, threshold=0.8)
        if self.free_remain:
            logger.info(f"[Free opportunities remain] {self.free_remain}")
        return self.free_remain
    
    @property
    def dateline(self) -> bool:
        result = self.appear(DATELINE_CHECK, offset=10, threshold=0.5)
        if result:
            logger.info("[Coop has expired]")
        return result
    
    @Config.when(EVENT_COOP=False)
    def ensure_into_coop(self, skip_first_screenshot=True):
        '''普通协同，从banner进入作战'''
        logger.hr('COOP START')
        coop_enter = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if not coop_enter:
                # 滑动banner查找协同作战
                self.ensure_sroll((260, 150), (30, 150), speed=35, count=1, delay=0.5)
                self.device.screenshot()
                banner_first = Button(EVENT_BANNER.area, None, button=EVENT_BANNER.area)
                banner_first._match_init = True
                banner_first.image = crop(self.device.image, EVENT_BANNER.area)
                while 1:
                    if skip_first_screenshot:
                        skip_first_screenshot = False
                    else:
                        self.device.screenshot()

                    tmp_image = self.device.image
                    # 滑动到下一个banner
                    self.ensure_sroll((260, 150), (30, 150), speed=35, count=1, delay=0.5)
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
                        logger.info("Not find coop in banner")
                        raise CoopIsUnavailable

                    if self.appear_then_click(COOP_BANNER_CHECK, offset=10, interval=2):
                        logger.info("Find coop in banner")
                        coop_enter = True
                        break

            if self.appear(COOP_CHECK, offset=10):
                break

        if self.free_opportunity_remain and not self.dateline:
            self.start_coop()
        else:
            logger.info("There are no free opportunities")

    @Config.when(EVENT_COOP=True)
    def ensure_into_coop(self, skip_first_screenshot=True):
        '''大型活动，从活动页面进入作战页面'''
        logger.hr('EVENT COOP START')
        click_timer = Timer(0.3)
        
        # 走到协同作战
        direct = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() \
                    and self.appear(EVENT_CHECK, offset=10, interval=5) \
                    and self.appear_then_click(COOP_ENTER, offset=10, interval=5):
                click_timer.reset()
                continue

            # 协同未在开启时间
            if click_timer.reached() \
                    and self.appear(COOP_LOCK, offset=10):
                logger.info("Coop is not enabled")
                raise CoopIsUnavailable

            # 协同选择
            if self.appear(COOP_SELECT_CHECK, offset=10):
                break

            # 协同主页
            if self.appear(COOP_CHECK, offset=10):
                direct = True
                break
        
        # 检查是否有开启的协同
        coops = TEMPLATE_COOP_ENABLE.match_multi(self.device.image, name='COOP_ENABLE')
        if not coops and not direct:
            logger.info("Not find coop in event")
            raise CoopIsUnavailable

        # 进入协同作战界面
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 选择协同
            if click_timer.reached() \
                    and self.appear(COOP_SELECT_CHECK, offset=10, interval=5):
                self.device.click(coops[0])
                click_timer.reset()
                continue

            # 协同主页
            if self.appear(COOP_CHECK, offset=10):
                break

        if self.free_opportunity_remain and not self.dateline:
            self.start_coop()
        else:
            logger.info("There are no free opportunities")

    def start_coop(self, skip_first_screenshot=True):
        logger.hr("Start a coop")
        click_timer = Timer(0.3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 选择普通难度
            if click_timer.reached() \
                    and self.appear(SECECT_DIFFICULTY, offset=10) \
                    and self.appear_then_click(DIFFICULTY_NORMAL_NOT_CHECKED, offset=10, interval=1):
                click_timer.reset()
                continue

            # 确认难度
            if click_timer.reached() \
                    and self.appear(SECECT_DIFFICULTY, offset=10) \
                    and self.appear(DIFFICULTY_NORMAL, offset=10) \
                    and self.appear_then_click(DIFFICULTY_CONFIRM, offset=10, interval=1):
                click_timer.reset()
                continue

            # 协同开始
            if click_timer.reached() \
                    and self.appear(COOP_ROLE_CHECK, offset=10) \
                    and not self.appear(COOP_CANCEL, offset=10) \
                    and self.appear_then_click(COOP_START, offset=10, interval=10, threshold=0.3):
                self.device.sleep(1)
                click_timer.reset()
                continue

            # 接受
            if click_timer.reached() \
                    and self.appear_then_click(COOP_ACCEPT, offset=30, interval=1):
                click_timer.reset()
                continue

            # TODO 选择妮姬
            # if click_timer.reached() \
            #         and self.appear_then_click(COOP_START, offset=10, interval=1):
            #     click_timer.reset()
            #     continue

            # 准备
            if click_timer.reached() \
                    and self.appear_then_click(COOP_PREPARE, offset=10, interval=1):
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

            # 结束
            if click_timer.reached() \
                    and self.appear_then_click(END_FIGHTING, offset=10, interval=1):
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
            logger.info("There are no free opportunities")
            raise NoOpportunityRemain

    def run(self):
        try:
            self.ui_ensure(page_main)
            if self.config.Coop_EventCoop:
                self.ui_ensure(page_event)
            self.ensure_into_coop()
        except CoopIsUnavailable:
            pass
        except NoOpportunityRemain:
            pass

        self.config.task_delay(server_update=True)
