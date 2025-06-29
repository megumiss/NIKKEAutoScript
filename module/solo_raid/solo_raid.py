from module.base.decorator import Config
from module.base.timer import Timer
from module.base.utils import crop
from module.logger import logger
from module.simulation_room.assets import AUTO_SHOOT, AUTO_BURST, END_FIGHTING
from module.solo_raid.assets import *
from module.ui.assets import GOTO_BACK, MAIN_CHECK
from module.ui.ui import UI
from module.ocr.ocr import Digit

class NoOpportunityRemain(Exception):
    pass

class SoloRaidIsUnavailable(Exception):
    pass

class SoloRaid(UI):
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

    def ensure_into_soloraid(self, skip_first_screenshot=True):
        '''进入单人突击'''
        logger.hr('SOLO RAID START')
        click_timer = Timer(0.3)
        confirm_timer = Timer(3, count=3).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if click_timer.reached() \
                    and self.appear(MAIN_CHECK, offset=10) \
                    and self.appear_then_click(SOLO_RAID, offset=10, interval=2):
                logger.info("Enter solo raid")
                continue

            if self.appear(SOLO_RAID_CHECK, offset=10):
                break

            if confirm_timer.reached():
                logger.error("Solo raid not found")
                raise SoloRaidIsUnavailable

        if self.free_opportunity_remain:
            self.solo_raid()
        else:
            logger.info("There are no free opportunities")

    def solo_raid(self, skip_first_screenshot=True):
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
            self.ensure_into_soloraid()
        except SoloRaidIsUnavailable:
            pass
        except NoOpportunityRemain:
            pass

        self.config.task_delay(server_update=True)
