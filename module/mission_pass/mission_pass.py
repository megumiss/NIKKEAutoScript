from module.base.timer import Timer
from module.base.utils import crop
from module.logger import logger
from module.mission_pass.assets import *
from module.ui.assets import MAIN_CHECK
from module.ui.page import page_main
from module.ui.ui import UI


class MissionPass(UI):
    def receive(self, skip_first_screenshot=True):
        click_timer = Timer(0.3)
        # flag = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 任务页面
            if (
                click_timer.reached()
                and self.appear(COMPLETED_CHECK, offset=30, threshold=0.9)
                and self.appear_then_click(PASS_MISSION, offset=30, threshold=0.91, interval=1)
            ):
                click_timer.reset()
                continue

            # 任务全部领取
            if (
                click_timer.reached()
                and self.appear(COMPLETED_CHECK, offset=30, threshold=0.9)
                and self.appear(PASS_REWARD, offset=30, threshold=0.9, static=False)
                and not self.appear(PASS_NO_REWARD, offset=30, threshold=0.9, static=False)
            ):
                self.device.click_minitouch(360, 1190)
                # flag = True
                self.device.sleep(1)
                logger.info('Reward pass mission')
                click_timer.reset()
                continue

            # 升级
            if click_timer.reached() and self.appear(RANK_UP_CHECK, offset=5, interval=1, static=False):
                self.device.click_minitouch(1, 1)
                click_timer.reset()
                continue

            # 奖励页面
            if (
                click_timer.reached()
                and self.appear(PASS_REWARD, offset=30, threshold=0.9, static=False)
                and not self.appear(DOT, offset=10, threshold=0.9)
                and self.appear_then_click(PASS_REWARD, offset=30, threshold=0.9, interval=1)
            ):
                click_timer.reset()
                continue

            # 奖励全部领取
            if (
                click_timer.reached()
                and not self.appear(COMPLETED_CHECK, offset=30, threshold=0.9)
                and self.appear(PASS_MISSION, offset=30, threshold=0.9, static=False)
                and not self.appear(PASS_NO_REWARD, offset=30, threshold=0.9, static=False)
            ):
                self.device.click_minitouch(360, 1190)
                self.device.sleep(1)
                logger.info('Reward pass reward')
                click_timer.reset()
                continue

            # 奖励领取
            if click_timer.reached() and self.handle_reward(1):
                click_timer.reset()
                continue

            # 关闭
            if (
                self.appear(PASS_CHECK, offset=5, static=False)
                and not self.appear(COMPLETED_CHECK, offset=30, threshold=0.9)
                and self.appear(PASS_NO_REWARD, offset=30, threshold=0.9, static=False)
                and self.appear(PASS_MISSION, offset=30, threshold=0.9, static=False)
            ):
                logger.info('Close misson pass')
                self.device.click_minitouch(1, 1)
                break

    def run(self):
        self.ui_ensure(page_main)
        skip_first_screenshot = True
        click_timer = Timer(0.3)

        # 获取当前pass数量
        self.config.PASS_LIMIT = 1
        if self.appear(CHANGE, offset=5, static=False) or self.appear(EXPAND, offset=5, static=False):
            # 第一个banner
            self.ensure_sroll((640, 200), (500, 200), speed=35, count=1, delay=0.5)
            self.device.screenshot()
            banner_first = Button(PASS_BANNER.area, None, button=PASS_BANNER.area)
            banner_first._match_init = True
            banner_first.image = crop(self.device.image, PASS_BANNER.area)
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                tmp_image = self.device.image
                # 滑动到下一个pass
                self.ensure_sroll((640, 200), (500, 200), speed=35, count=1, delay=0.5)
                # 比较banner是否变化
                while 1:
                    self.device.screenshot()

                    banner = Button(PASS_BANNER.area, None, button=PASS_BANNER.area)
                    banner._match_init = True
                    banner.image = crop(tmp_image, PASS_BANNER.area)
                    if not self.appear(banner, offset=10, threshold=0.8):
                        logger.info(f'Find mission pass {self.config.PASS_LIMIT}')
                        self.config.PASS_LIMIT += 1
                        break
                    else:
                        continue
                # 回到第一个banner
                if self.appear(banner_first, offset=10, threshold=0.6):
                    self.config.PASS_LIMIT -= 1
                    break

        logger.attr('PENDING MISSION PASS', self.config.PASS_LIMIT)
        passs = self.config.PASS_LIMIT
        while 1:
            find_dot = False
            # 每次都检查所有的pass
            for _ in range(passs):
                self.device.screenshot()
                # 检查红点
                if click_timer.reached() and self.appear_then_click(DOT, offset=5):
                    find_dot = True
                    self.receive()
                    while 1:
                        self.device.screenshot()
                        if self.appear(MAIN_CHECK, offset=5, interval=0.3):
                            break
                    self.config.PASS_LIMIT -= 1
                    logger.attr('PENDING MISSION PASS', self.config.PASS_LIMIT)
                    break
                passs -= 1
                self.ensure_sroll((640, 200), (500, 200), speed=35, count=1, delay=0.5)

            # 没有红点
            if not find_dot:
                logger.info('ALL MISSION PASS DONE')
                break

        self.config.task_delay(server_update=True)
