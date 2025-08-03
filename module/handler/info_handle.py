from module.base.base import ModuleBase
from module.base.utils import color_similar, get_color, point2str

# from module.event.event_5.assets import SKIP, TOUCH_TO_CONTINUE
from module.exception import GameServerUnderMaintenance, GameStuckError
from module.handler.assets import *
from module.interception.assets import TEMPLATE_RED_CIRCLE
from module.logger import logger


class InfoHandler(ModuleBase):
    def handle_paid_gift(self, interval=1):
        if self.appear(PAID_GIFT_CHECK, offset=(30, 30), interval=interval, static=False):
            if self.appear_text_then_click('点击关闭画面', interval=interval):
                return True

        elif self.appear(PAID_GIFT_CONFIRM_CHECK, offset=(30, 30), interval=interval, static=False):
            if self.appear_then_click(CONFIRM_B, offset=(30, 30), interval=interval, static=False):
                return True

        return False

    def handle_login_reward(self):
        if CLOSE_DAILY_LOGIN.appear_on(self.device.image):
            self.device.click_minitouch(1, 420)
            self.device.sleep(1)
            return True
        # Daily Login, Memories Spring, Monthly Card, etc.
        if self.appear_text_then_click('_领取奖励', interval=3):
            return True
        elif a := self.appear_text('_全部领取', interval=3):
            x, y = a[0], a[1]
            b = get_color(
                image=self.device.image,
                area=(int(x - 80), int(y - 25), int(x + 80), int(y + 25)),
            )
            if not color_similar(color1=b, color2=(112.786625, 111.897375, 113.121875), threshold=10):
                self.device.click_minitouch(x, y)
                return True
            else:
                self.device.click_minitouch(1, 420)
                self.device.sleep(1)

    # 屑芙蒂的补给品，仅关闭窗口，不抽取
    def handle_shifty_supplies(self):
        # TODO 领取每日奖励

        # 关闭
        if self.appear(
            SHIFTY_SUPPLIES_CHECK,
            offset=(30, 30),
            interval=3,
            threshold=0.74,
            static=False,
        ) and self.appear_then_click(
            SHIFTY_SUPPLIES_CLOSE,
            offset=(30, 30),
            interval=3,
            threshold=0.74,
            static=False,
        ):
            return True

        """
            出现登录奖励时，点击没有被覆盖的位置 
            Daily Login, Memories Spring, etc.
        """
        # 420:550, 230:700
        # if self._appear_text_then_click('根据累积登入天数', (20, 600), 'CLOSE_DAILY_LOGIN_A', interval=1,
        #                                 area=(230, 420, 700, 550)):
        #     self.device.sleep(3)
        #     return True
        #
        # if self._appear_text_then_click('根据累积登入天数', (20, 600), 'CLOSE_DAILY_LOGIN_B', interval=1,
        #                                 area=(165, 255, 560, 290)):
        #     self.device.sleep(3)
        #     return True
        #
        # if self._appear_text_then_click('根据累积登入天数', (20, 600), 'CLOSE_DAILY_LOGIN_C', interval=1,
        #                                 area=(165, 300, 570, 340)):
        #     self.device.sleep(3)
        #     return True
        #
        # if self._appear_text_then_click('根据累积登入天数', (20, 600), 'CLOSE_DAILY_LOGIN_D', interval=1,
        #                                 area=(430, 380, 740, 420)):
        #     self.device.sleep(3)
        #     return True
        #
        # if self.appear_then_click(CLOSE_DAILY_LOGIN_C, offset=(30, 30), interval=5, static=False):
        #     self.device.sleep(2)
        #     return True

        return False

    def handle_reward(self, interval=5):
        if self.appear_then_click(REWARD, offset=(30, 30), interval=interval, static=False):
            return True

    def handle_level_up(self, interval=3):
        if self.appear(LEVEL_UP_CHECK, offset=(30, 30), interval=interval):
            self.device.click_minitouch(360, 920)
            logger.info('Click (360, 920) @ LEVEL_UP')
            return True

    def handle_server(self):
        if self.appear(SERVER_CHECK, offset=(30, 30), interval=3, static=False) and self.appear_then_click(
            CONFIRM_A, offset=(30, 30), interval=3, static=False
        ):
            return True

    def handle_popup(self):
        if self.appear(POPUP_CHECK, offset=(30, 30), interval=3, static=False) and self.appear_then_click(
            ANNOUNCEMENT, offset=(30, 30), interval=3, threshold=0.74, static=False
        ):
            return True

    def handle_announcement(self):
        if self.appear(
            ANNOUNCEMENT_CHECK,
            offset=(30, 30),
            interval=3,
            threshold=0.74,
            static=False,
        ) and self.appear_then_click(ANNOUNCEMENT, offset=(30, 30), interval=3, threshold=0.74, static=False):
            return True

    def handle_download(self):
        if self.appear(DOWNLOAD_CHECK, offset=(30, 30), interval=3, static=False) and self.appear_then_click(
            CONFIRM_A, offset=(30, 30), interval=3, static=False
        ):
            return True

    def handle_system_error(self):
        if self.appear(SYSTEM_ERROR_CHECK, offset=(30, 30), interval=3, static=False):
            raise GameStuckError('detected system error')

    def handle_system_maintenance(self):
        if self.appear(SYSTEM_MAINTENANCE_CHECK, offset=(30, 30), interval=3, static=False):
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
        if self.appear(LOGIN_CHECK, offset=(30, 30), interval=5) or self.appear(
            LOGIN_CHECK_B, offset=(30, 30), interval=5
        ):
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
