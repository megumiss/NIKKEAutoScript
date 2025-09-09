import time

from module.base.base import ModuleBase
from module.base.timer import Timer
from module.base.utils import point2str

# from module.event.event_5.assets import SKIP, TOUCH_TO_CONTINUE
from module.exception import GameServerUnderMaintenance, GameStuckError
from module.handler.assets import *
from module.interception.assets import TEMPLATE_RED_CIRCLE
from module.logger import logger
from module.ui.assets import GOTO_BACK, MAIN_CHECK


class InfoHandler(ModuleBase):
    def handle_paid_gift(self, interval=1):
        """礼包弹窗"""
        if self.appear(PAID_GIFT_CHECK, offset=(30, 30)) and self.appear_then_click(
            CLICK_TO_CLOSE, offset=30, interval=interval
        ):
            return True

        if self.appear(PAID_GIFT_CONFIRM_CHECK, offset=(30, 30)) and self.appear_then_click(
            CONFIRM_B, offset=(30, 30), interval=interval
        ):
            return True

        return False

    def handle_login_reward(self):
        # 添加限速机制 - 检查是否在冷却时间内
        current_time = time.time()
        # 5秒冷却时间
        if hasattr(self, '_last_login_reward_check') and current_time - self._last_login_reward_check < 5:
            return False

        # 更新最后检查时间
        self._last_login_reward_check = current_time

        # Daily Login, Memories Spring, Monthly Card, etc.
        reward = self.appear_text('全部领取')
        if reward:
            # 重置限速机制，因为有奖励可领取
            self._last_login_reward_check = 0

            x, y = reward[0], reward[1]
            logger.info('Click %s @ 全部领取' % point2str(x, y))
            self.device.click_minitouch(x, y)

            reward_done = False
            confirm_timer = Timer(2, count=3)
            while 1:
                self.device.screenshot()

                # 领取完奖励，返回主界面
                if reward_done:
                    if self.appear(MAIN_CHECK, offset=30):
                        logger.info('Page arrive: main')
                        return True

                    if self.appear(GOTO_BACK, offset=30):
                        if self.appear_then_click(GOTO_BACK, offset=30, interval=1):
                            logger.info('Back to main page')
                            continue
                    else:
                        # 点击空白页
                        self.device.click_minitouch(1, 420)
                        self.device.sleep(1)
                        logger.info('Click %s @ CLOSE' % point2str(1, 420))
                        continue

                # 无奖励可领
                if self.appear(NO_REWARD, offset=30):
                    logger.info('Reward done')
                    reward_done = True
                    confirm_timer.clear()
                    continue
                else:
                    if not confirm_timer.started():
                        confirm_timer.start()
                    # 超过2秒没有出现无奖励可领，返回点击Reward
                    if confirm_timer.reached():
                        return True
        else:
            return False

    # 屑芙蒂的补给品，仅关闭窗口，不抽取
    def handle_shifty_supplies(self):
        if self.appear(SHIFTY_SUPPLIES_CHECK, offset=(30, 30)) and self.appear_then_click(
            SHIFTY_SUPPLIES_CLOSE, offset=(30, 30), interval=3
        ):
            return True

        return False

    def handle_reward(self, interval=5):
        if self.appear_then_click(REWARD, offset=(30, 30), interval=interval, static=False):
            return True

    def handle_level_up(self):
        if self.appear(LEVEL_UP_CHECK, offset=(30, 30)):
            self.device.click_minitouch(360, 920)
            self.device.sleep(1)
            logger.info('Click (360, 920) @ LEVEL_UP')
            return True

    def handle_server(self):
        if self.appear(SERVER_CHECK, offset=(30, 30), static=False) and self.appear_then_click(
            CONFIRM_A, offset=(30, 30), interval=3, static=False
        ):
            return True

    def handle_popup(self):
        if self.appear(POPUP_CHECK, offset=(30, 30), static=False) and self.appear_then_click(
            ANNOUNCEMENT, offset=(30, 30), interval=3, threshold=0.74, static=False
        ):
            return True

    def handle_announcement(self):
        if self.appear(ANNOUNCEMENT_CHECK, offset=(30, 30), threshold=0.74, static=False) and self.appear_then_click(
            ANNOUNCEMENT, offset=(30, 30), interval=3, threshold=0.74, static=False
        ):
            return True

    def handle_download(self):
        if self.appear(DOWNLOAD_CHECK, offset=(30, 30), static=False) and self.appear_then_click(
            CONFIRM_A, offset=(30, 30), interval=3, static=False
        ):
            return True

    def handle_downloading(self):
        if self.appear(DOWNLOADING_CHECK, offset=30):
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            self.device.sleep(10)
            return True

    def handle_system_error(self):
        if self.appear(SYSTEM_ERROR_CHECK, offset=(30, 30), static=False):
            raise GameStuckError('detected system error')

    def handle_system_maintenance(self):
        if self.appear(SYSTEM_MAINTENANCE_CHECK, offset=(30, 30), static=False):
            raise GameServerUnderMaintenance('Server is currently under maintenance')

    # def handle_event(self, interval=3):
    #     if self.appear_then_click(SKIP, offset=(5, 5), static=False, interval=interval):
    #         return True
    #     elif self.appear_then_click(
    #             TOUCH_TO_CONTINUE, offset=(5, 5), static=False, interval=interval
    #     ):
    #         self.device.click_minitouch(360, 720)
    #         return True

    def handle_login(self):
        if self.appear(LOGIN_CHECK, offset=(30, 30)) or self.appear(LOGIN_CHECK_B, offset=(30, 30)):
            self.device.click(LOGIN_CHECK)
            logger.info('Login success')

    def handle_red_circles(self):
        """
        处理红圈
        """
        circles = TEMPLATE_RED_CIRCLE.match_multi(self.device.image, similarity=0.65, name='RED_CIRCLE')
        for circle in circles:
            x = circle.location[0]
            y = circle.location[1]
            if x < 75 or y > 1000:
                continue

            # 因为画面变动添加的偏移
            if x < 300:
                x_click = x + 90
            elif x > 400:
                x_click = x - 90
            else:
                x_click = x

            # 坐标识别偏移
            y_click = y + 40
            logger.info('Click %s @ %s' % (point2str(x_click, y_click), 'RED_CIRCLE'))
            self.device.long_click_minitouch(x_click, y_click, 1)
            # 画面回正
            self.device.sleep(0.5)
            return True

        return False
