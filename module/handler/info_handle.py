import time

from module.base.base import ModuleBase
from module.base.langs import Langs
from module.base.timer import Timer
from module.base.utils import point2str

# from module.event.event_5.assets import SKIP, TOUCH_TO_CONTINUE
from module.exception import GameServerUnderMaintenance, GameStuckError
from module.handler.assets import *
from module.interception.assets import TEMPLATE_RED_CIRCLE_LEFT, TEMPLATE_RED_CIRCLE_RIGHT, TEMPLATE_RED_CIRCLE_TOP
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
        reward = self.appear_text(Langs.CLAIM_ALL)
        if reward:
            # 重置限速机制，因为有奖励可领取
            self._last_login_reward_check = 0

            x, y = reward[0], reward[1]
            logger.info(f'Click {point2str(x, y)} @ {Langs.CLAIM_ALL}')
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
            CONFIRM_A, offset=(30, 30), threshold=0.7, interval=3, static=False
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

    def calculate_click_position(self, x, y, template_type, k=0.4, x_center=360, screen_width=720, screen_height=1280):
        """
        计算点击位置，支持不同类型模板的额外偏移
        Args:
            x, y: 原始识别坐标
            template_type: 模板类型，可为 'TOP', 'RIGHT', 'LEFT'
            k: 动态偏移系数
            x_center: 屏幕中心 x
            screen_width: 屏幕宽度
            screen_height: 屏幕高度
        Returns:
            (x_click, y_click, dx): 点击坐标和动态偏移值
        """
        # 动态偏移
        dx = k * (x_center - x)
        x_click = x + dx
        y_click = y

        # 根据模板类型增加额外偏移
        if template_type == 'TOP':
            y_click += 50
        elif template_type == 'RIGHT':
            x_click -= 50
        elif template_type == 'LEFT':
            x_click += 50

        # 防止超出屏幕边界
        x_click = max(0, min(screen_width, x_click))
        y_click = max(0, min(screen_height, y_click))
        if x_click <= 0 or x_click >= screen_width:
            if template_type == 'RIGHT':
                x_click += 30
            elif template_type == 'LEFT':
                x_click -= 30

        # 坐标取整
        return int(round(x_click)), int(round(y_click)), dx

    def handle_red_circles(self):
        """
        处理红圈（优先检测 TOP，若未检测到再检测 RIGHT / LEFT）
        """
        # 模型参数
        k = 0.4

        # 按优先顺序定义模板
        templates = [
            ('TOP', TEMPLATE_RED_CIRCLE_TOP, 0.65),
            ('RIGHT', TEMPLATE_RED_CIRCLE_RIGHT, 0.75),
            ('LEFT', TEMPLATE_RED_CIRCLE_LEFT, 0.65),
        ]

        for template_type, template, similarity in templates:
            circles = template.match_multi(self.device.image, similarity=similarity, name=f'RED_CIRCLE_{template_type}')

            # 若当前模板检测到红圈则立即处理，否则尝试下一个模板
            if not circles:
                continue

            for circle in circles:
                logger.info(f'Circle {template_type} position: {circle.location}')
                x = circle.location[0]
                y = circle.location[1]
                if y < 75 or y > 850:
                    continue

                x_click, y_click, dx = self.calculate_click_position(x, y, template_type=template_type, k=k)
                logger.info(
                    'Click %s @ %s (dx=%.2f)' % (point2str(x_click, y_click), f'RED_CIRCLE_{template_type}', dx)
                )
                self.device.long_click_minitouch(x_click, y_click, 1)

                # 画面回正
                self.device.sleep(0.5)
                return True

            continue

        return False
