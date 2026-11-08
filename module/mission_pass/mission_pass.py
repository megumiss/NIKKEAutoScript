import copy

import cv2

from module.base.button import shift_button
from module.base.langs import Langs
from module.base.timer import Timer
from module.base.utils import crop
from module.handler.assets import REWARD
from module.logger import logger
from module.mission_pass.assets import *
from module.ui.assets import MAIN_CHECK, MAIN_GOTO_FRIEND
from module.ui.page import page_main
from module.ui.ui import UI


class MissionPass(UI):
    def receive(self, skip_first_screenshot=True):
        logger.hr('Receive pass', 2)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 任务标签页有红点，先打开打开任务页面
            if (
                self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50))
                # 当前在奖励标签页
                and self.appear_text(Langs.PASS_REWARD_TAB, threshold=1)
            ):
                # 点击相对于红点的偏移位置
                self.appear_then_click(
                    MISSION_RED_POINT, offset=(-25, -50, 10, 50), click_offset=(-150, 20), interval=1
                )
                continue

            # 任务标签页没有红点，奖励标签页有红点，返回奖励页面
            if (
                not self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50))
                and self.appear(REWARD_RED_POINT, offset=(-25, -50, 10, 50))
                # 当前在任务标签页
                and not self.appear_text(Langs.PASS_REWARD_TAB, threshold=1)
            ):
                # 点击相对于红点的偏移位置
                self.appear_then_click(REWARD_RED_POINT, offset=(-25, -50, 10, 50), click_offset=(-150, 20), interval=1)
                continue

            # 任务全部领取
            if self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50)) and self.appear_text(
                Langs.PASS_MISSION_TAB, threshold=1
            ):
                if self.appear_text_then_click(Langs.CLAIM_ALL, threshold=0.7, interval=1):
                    self.device.sleep(1)
                logger.info('Reward pass mission')
                continue

            # 升级
            if self.appear_then_click(RANK_UP_CHECK, offset=30, click_offset=(0, 200), interval=1):
                continue

            # 奖励全部领取
            if (
                not self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50))
                and self.appear(REWARD_RED_POINT, offset=(-25, -50, 10, 50))
                and self.appear_text(Langs.PASS_REWARD_TAB, threshold=1)
            ):
                if self.appear_text_then_click(Langs.CLAIM_ALL, threshold=0.7, interval=1):
                    self.device.sleep(1)
                logger.info('Reward pass reward')
                continue

            # 奖励领取
            if self.appear_then_click(REWARD, offset=30, interval=1, static=False):
                continue

            # 关闭
            if (
                (self.appear_text(Langs.PASS_CHECK, threshold=0.9) or self.appear_text(Langs.PASS_CHECK_2, threshold=0.9))
                and not self.appear(MISSION_RED_POINT, offset=(-25, -50, 10, 50))
                and not self.appear(REWARD_RED_POINT, offset=(-25, -50, 10, 50))
            ):
                self.device.click_xy(5, 5)
                self.device.sleep(0.5)
                continue

            # 回到主页面
            if self.appear(MAIN_CHECK, offset=10):
                logger.info('Close misson pass')
                # 等待右下角loading消失
                self.ui_wait_loading()
                break

    def pass_popup_appear(self):
        """
        滑动被识别为点击时会打开pass弹窗
        """
        if self.appear(MAIN_CHECK, offset=30):
            return False
        return self.appear_text(Langs.PASS_CHECK, threshold=0.9) or self.appear_text(Langs.PASS_CHECK_2, threshold=0.9)

    def wait_banner_change(self, image, area, timeout=20):
        """
        等待banner相对image发生变化

        Returns:
            str: 'changed' banner变化,
                 'popup' 滑动打开了pass弹窗，已领取或关闭,
                 'timeout' 超时未变化
        """
        banner = Button(area, None, button=area)
        banner._match_init = True
        banner.image = crop(image, area)
        # 正常滑动约8秒内变化
        timer = Timer(timeout, count=10).start()
        while 1:
            self.device.screenshot()

            # 弹窗会盖住banner，先于banner比较检查
            if self.pass_popup_appear():
                logger.info('Swipe opened mission pass popup')
                self.receive()
                return 'popup'
            if not self.appear(banner, offset=10, threshold=0.8):
                return 'changed'
            if timer.reached():
                logger.warning('Mission pass banner not changed, wait timeout')
                return 'timeout'

    def wait_pass_popup(self, timeout=5):
        """
        被识别为点击的滑动约3秒后才打开pass弹窗，期间主页面不变

        Returns:
            bool: 是否出现弹窗，已领取或关闭
        """
        timer = Timer(timeout, count=8).start()
        while not timer.reached():
            self.device.screenshot()
            if self.pass_popup_appear():
                logger.info('Swipe opened mission pass popup')
                self.receive()
                return True
        return False

    def run(self):
        self.ui_ensure(page_main)
        # 等待右下角loading消失
        self.ui_wait_loading()

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
        # 只有EXPAND时数量是估算的
        pass_count_exact = True
        if self.appear(CHANGE, offset=70):
            self.config.PASS_LIMIT = 2
        elif self.appear(EXPAND, offset=70):
            pass_count_exact = False
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
                # 比较banner是否变化，弹窗或超时视为已数完
                if self.wait_banner_change(tmp_image, PASS_BANNER_DYNAMIC.area) != 'changed':
                    break
                logger.info(f'Find mission pass {self.config.PASS_LIMIT}')
                self.config.PASS_LIMIT += 1
                # 回到第一个banner
                if self.appear(banner_first, offset=10, threshold=0.6):
                    self.config.PASS_LIMIT -= 1
                    break
            if self.config.PASS_LIMIT < 3:
                self.config.PASS_LIMIT = 4

        logger.attr('PENDING MISSION PASS', self.config.PASS_LIMIT)
        passs = self.config.PASS_LIMIT
        while 1:
            find_dot = False
            # 每次都检查所有的pass
            if not passs == 1:
                self.ensure_sroll(
                    (650, pass_scrol_y), (500, pass_scrol_y), method='swipe', speed=40, count=1, delay=0.5
                )
            for i in range(passs * 2):
                self.device.screenshot()
                if self.appear(DOT, offset=dot_offset):
                    find_dot = True
                    while 1:
                        self.device.screenshot()

                        # pass弹窗
                        if self.appear_text(Langs.PASS_CHECK, threshold=0.9) or self.appear_text(
                            Langs.PASS_CHECK_2, threshold=0.9
                        ):
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
                    # 上一次滑动打开了pass弹窗，领取或关闭后再比较banner
                    if self.pass_popup_appear():
                        self.receive()
                        continue
                    # 最后一次不需要滑动
                    if i == passs * 2 - 1:
                        break
                    tmp_image = self.device.image
                    self.ensure_sroll(
                        (650, pass_scrol_y), (500, pass_scrol_y), method='swipe', speed=40, count=1, delay=0.5
                    )
                    # 比较banner是否变化，超时说明无法滑动，结束本轮
                    if self.wait_banner_change(tmp_image, PASS_BANNER_DYNAMIC.area) == 'timeout':
                        break

            # 没有红点，或数量准确时已全部领取
            if not find_dot or (pass_count_exact and self.config.PASS_LIMIT <= 0):
                logger.info('ALL MISSION PASS DONE')
                break

        # 扫描时滑动过，结束前确认没有迟到的弹窗
        if not find_dot and passs > 1:
            self.wait_pass_popup()
        self.config.task_delay(schedule=True)
