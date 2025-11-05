import copy

import cv2

from module.base.button import shift_button
from module.base.timer import Timer
from module.base.utils import crop
from module.handler.assets import REWARD
from module.logger import logger
from module.mission_pass.assets import *
from module.ui.assets import MAIN_CHECK, MAIN_GOTO_FRIEND
from module.ui.page import page_main
from module.ui.ui import UI

PASS_MISSION_BUTTONS = [PASS_MISSION, PASS_MISSION_2]
PASS_REWARD_BUTTONS = [PASS_REWARD, PASS_REWARD_2]


class MissionPass(UI):
    def receive(self, skip_first_screenshot=True):
        click_timer = Timer(0.3)
        # flag = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 打开任务页面
            if (
                click_timer.reached()
                and self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50))
                and self.appear_then_click_any(PASS_MISSION_BUTTONS, offset=30, interval=1)
            ):
                click_timer.reset()
                continue

            # 返回奖励页面
            if (
                click_timer.reached()
                and not self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50))
                and self.appear(REWARD_RED_POINT, offset=(-25, -50, 10, 50))
                and self.appear_then_click_any(PASS_REWARD_BUTTONS, offset=30, interval=1)
            ):
                click_timer.reset()
                continue

            # 任务全部领取
            if (
                click_timer.reached()
                and self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50))
                and self.appear_any(PASS_MISSION_BUTTONS, offset=30)
            ):
                self.device.click_minitouch(360, 1190)
                self.device.sleep(1)
                logger.info('Reward pass mission')
                click_timer.reset()
                continue

            # 升级
            if click_timer.reached() and self.appear_then_click(RANK_UP_CHECK, offset=30, interval=1):
                click_timer.reset()
                continue

            # 奖励全部领取
            if (
                click_timer.reached()
                and not self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50))
                and self.appear(REWARD_RED_POINT, offset=(-25, -50, 10, 50))
                and self.appear_any(PASS_REWARD_BUTTONS, offset=30)
            ):
                self.device.click_minitouch(360, 1190)
                self.device.sleep(1)
                logger.info('Reward pass reward')
                click_timer.reset()
                continue

            # 奖励领取
            if click_timer.reached() and self.appear_then_click(REWARD, offset=30, interval=1, static=False):
                click_timer.reset()
                continue

            # 关闭
            if (
                self.appear(PASS_CHECK, offset=30)
                and not self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50))
                and not self.appear(REWARD_RED_POINT, offset=(-25, -50, 10, 50))
            ):
                self.device.click_minitouch(1, 1)
                self.device.sleep(0.5)
                click_timer.reset()
                continue

            # 回到主页面
            if self.appear(MAIN_CHECK, offset=10):
                logger.info('Close misson pass')
                self.device.sleep(1)
                break

    def run(self):
        self.ui_ensure(page_main)
        skip_first_screenshot = True
        click_timer = Timer(0.3)
        pass_scrol_y = 200
        dot_offset = (20, 20)
        PASS_BANNER_DYNAMIC = copy.deepcopy(PASS_BANNER)

        # 根据好友图标计算pass滑动位置
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(MAIN_GOTO_FRIEND, offset=10):
                break
            else:
                if self.appear(MAIN_GOTO_FRIEND, offset=10, static=False):
                    # 向下偏移60个像素
                    pass_scrol_y = pass_scrol_y + 60
                    dot_offset = (0, 60, 0, 60)
                    PASS_BANNER_DYNAMIC = shift_button(PASS_BANNER, 0, 60)
                    break

        # 获取当前pass数量
        self.config.PASS_LIMIT = 1
        if self.appear(CHANGE, offset=5, static=False) or self.appear(EXPAND, offset=5, static=False):
            # 第一个banner
            self.ensure_sroll((650, pass_scrol_y), (500, pass_scrol_y), method='swipe', speed=40, count=1, delay=0.5)
            self.device.screenshot()
            banner_first = Button(PASS_BANNER_DYNAMIC.area, None, button=PASS_BANNER_DYNAMIC.area)
            banner_first._match_init = True
            banner_first.image = crop(self.device.image, PASS_BANNER_DYNAMIC.area)
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                tmp_image = self.device.image
                # 滑动到下一个pass
                self.ensure_sroll(
                    (650, pass_scrol_y), (500, pass_scrol_y), method='swipe', speed=40, count=1, delay=0.5
                )
                # 比较banner是否变化
                while 1:
                    self.device.screenshot()

                    banner = Button(PASS_BANNER_DYNAMIC.area, None, button=PASS_BANNER_DYNAMIC.area)
                    banner._match_init = True
                    banner.image = crop(tmp_image, PASS_BANNER_DYNAMIC.area)
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
            if not passs == 1:
                self.ensure_sroll(
                    (650, pass_scrol_y), (500, pass_scrol_y), method='swipe', speed=40, count=1, delay=0.5
                )
            for _ in range(passs * 2):
                self.device.screenshot()
                if self.appear(DOT, offset=dot_offset):
                    find_dot = True
                    while 1:
                        self.device.screenshot()

                        # pass弹窗
                        if self.appear(PASS_CHECK, offset=30):
                            logger.info('Open misson pass')
                            break

                        # 进入某个pass
                        if (
                            click_timer.reached()
                            and self.appear(MAIN_CHECK, offset=30)
                            and self.appear_then_click(DOT, offset=dot_offset, click_offset=(-20, 10), interval=3)
                        ):
                            click_timer.reset()
                            continue

                    # 领取pass
                    self.receive()
                    self.config.PASS_LIMIT -= 1
                    logger.attr('PENDING MISSION PASS', self.config.PASS_LIMIT)
                    break
                else:
                    if passs == 1:
                        break
                    tmp_image = self.device.image
                    self.ensure_sroll(
                        (650, pass_scrol_y), (500, pass_scrol_y), method='swipe', speed=40, count=1, delay=0.5
                    )
                    # 比较banner是否变化
                    while 1:
                        self.device.screenshot()

                        banner = Button(PASS_BANNER_DYNAMIC.area, None, button=PASS_BANNER_DYNAMIC.area)
                        banner._match_init = True
                        banner.image = crop(tmp_image, PASS_BANNER_DYNAMIC.area)
                        if not self.appear(banner, offset=10, threshold=0.8):
                            break
                        else:
                            continue

            # 没有红点
            if not find_dot:
                logger.info('ALL MISSION PASS DONE')
                break

        self.config.task_delay(server_update=True)
