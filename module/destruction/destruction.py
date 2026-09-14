from module.base.timer import Timer
from module.destruction.assets import *
from module.logger import logger
from module.ocr.ocr import Digit
from module.ui.assets import DESTROY_CHECK
from module.ui.page import page_destroy
from module.ui.ui import UI

# 歼灭按钮上显示的钻石消耗（即下一次歼灭的价格） -> 今日已歼灭次数
# 每日首次歼灭免费，之后单次消耗依次为 50/70/100/150/200/250/300/350/400/500
GEM_COST_TO_USED = {
    50: 0,
    70: 1,
    100: 2,
    150: 3,
    200: 4,
    250: 5,
    300: 6,
    350: 7,
    400: 8,
    500: 9,
}


class Destruction(UI):
    # 免费歼灭
    def destroy_free(self, skip_first_screenshot=True):
        logger.hr('Destruction')
        confirm_timer = Timer(2, count=3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear_then_click(DESTROY, offset=10, interval=3, static=False):
                continue

            # 处理领取 升级等
            if self.handle_level_up():
                continue
            if self.handle_reward(interval=1):
                continue
            if self.handle_paid_gift():
                continue

            if self.appear(DESTROY_CHECK, offset=10):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    break
            else:
                confirm_timer.clear()

        logger.info('Destruction free has finished')

    @property
    def GEM_USE_NEXT(self) -> int:
        """当前页面上「下一次歼灭」需要消耗的钻石数量"""
        model_type = self.config.Optimization_OcrModelType
        GEM_NEXT = Digit(
            [DESTROY_GEM.area],
            name='GEM_NEXT',
            model_type=model_type,
            lang='ch',
        )
        return int(GEM_NEXT.ocr(self.device.image)['text'])

    # 钻石歼灭
    def destroy_gem(self, skip_first_screenshot=True):
        logger.hr('Destruction gem')
        finish_timer = Timer(2, count=3)
        # 目标歼灭次数由配置给定，实际已歼灭多少次靠按钮上显示的钻石消耗反推
        target = self.config.Destruction_GemTimes
        finished = False

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(DESTROY_CHECK, offset=10):
                if self.appear(DESTROY_GEM_DONE, threshold=10):
                    logger.info(f'Gem destruction finished, times 10/{target}')
                    break
                if self.appear(DESTROY_GEM_CHECK, offset=10):
                    # 下一次的钻石消耗
                    cost = self.GEM_USE_NEXT
                    # 完成的歼灭次数
                    times = GEM_COST_TO_USED.get(cost)
                    if times is None:
                        logger.warning(f'Unexpected gem cost: {cost}, retry')
                        continue
                    if times >= target:
                        logger.info(f'Gem destruction finished, times {times}/{target}')
                        finished = True
                    else:
                        self.appear_then_click(DESTROY_GEM_CHECK, offset=10, interval=3)
                        continue

                if finished:
                    if not finish_timer.started():
                        finish_timer.start()
                    if finish_timer.reached():
                        break
                else:
                    finish_timer.clear()
            else:
                finish_timer.clear()

            # 处理领取 升级等
            if self.handle_level_up():
                continue
            if self.handle_reward(interval=1):
                continue
            if self.handle_paid_gift():
                continue
            # 同步钻石
            if self.appear_then_click(SYNC_GEM, offset=10, interval=1):
                continue
            # 确认
            if self.appear(DESTROY_GEM_BUY_CHECK, offset=10) and self.appear_then_click(
                DESTROY_GEM_BUY_CONFIRM, offset=10, interval=1
            ):
                continue

    def run(self):
        self.ui_ensure(page_destroy)
        self.destroy_free()
        if self.config.Destruction_UseGem:
            self.destroy_gem()
        self.config.task_delay(schedule=True)
