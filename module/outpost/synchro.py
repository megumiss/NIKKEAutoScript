from functools import cached_property
import re
from datetime import datetime, timedelta, timezone
from module.base.timer import Timer
from module.base.utils import point2str
from module.outpost.assets import *
from module.logger import logger
from module.ocr.ocr import Digit
from module.ui.assets import COMMISSION_CHECK
from module.ui.page import page_synchro
from module.ui.ui import UI

class Synchro(UI):
    @cached_property
    def next_tuesday(self) -> datetime:
        local_now = datetime.now()
        remain = (1 - local_now.weekday()) % 7
        remain = remain + 7 if remain == 0 else remain
        return local_now.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=remain) + self.diff
    
    def receive(self):
        logger.hr('Receive commission', 2)
        confirm_timer = Timer(3, count=5)
        click_timer = Timer(0.3)

        while 1:
            self.device.screenshot()

            if self.handle_reward(interval=1):
                click_timer.reset()
                continue

            # 全部领取
            if click_timer.reached() and self.appear_then_click(CLAIM, threshold=20, interval=1):
                click_timer.reset()
                continue

            # 等待领取完毕
            if self.appear(COMMISSION_CHECK, offset=10) and self.appear(CLAIM_DONE, threshold=20):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    # 需要派遣
                    if self.appear(DISPATCH, threshold=10):
                        logger.info('Receive commission done')
                        break
                    # 没次数/进行中
                    if self.appear(DISPATCH_DONE, threshold=10):
                        raise NoOpportunity
            else:
                confirm_timer.clear()


    def run(self):
        logger.hr('Synchro upgrade')
        self.ui_ensure(page_synchro)
        try:
            # 领取
            self.receive()
            
        except NoOpportunity:
            logger.warning('Commission running or allready done')

        self.config.task_delay(target=self.next_tuesday)
