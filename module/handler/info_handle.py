import time

from module.base.base import ModuleBase
from module.base.langs import Langs
from module.base.timer import Timer
from module.base.utils import point2str
from module.config.account import load_account

# from module.event.event_5.assets import SKIP, TOUCH_TO_CONTINUE
from module.exception import (
    AccountError,
    CaptchaRequired,
    GameServerUnderMaintenance,
    GameStart,
    GameStuckError,
    GameSystemError,
)
from module.handler.assets import *
from module.interception.assets import TEMPLATE_RED_CIRCLE_LEFT, TEMPLATE_RED_CIRCLE_RIGHT, TEMPLATE_RED_CIRCLE_TOP
from module.logger import logger
from module.ui.assets import GOTO_BACK, MAIN_CHECK


STUCK_RECOVERY_MINUTES = 3


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

    def handle_login_reward(self, in_login=False):
        # 收获页也有“获得奖励”，普通导航不能按这段文字领取。
        if not in_login:
            return False

        # 完成提示不是页面标识，只在本次领取后的短确认窗口内用于关闭。
        confirm_timer = getattr(self, '_login_reward_confirm_timer', None)
        if confirm_timer is not None and not confirm_timer.reached():
            if not self.appear_any([NO_REWARD, CANNOT_REWARD], offset=30):
                return False

            close_timer = self.interval_timer.setdefault('LOGIN_REWARD_CLOSE', Timer(1))
            if not close_timer.reached():
                return False

            if self.appear(GOTO_BACK, offset=30):
                if not self.appear_then_click(GOTO_BACK, offset=30, interval=1):
                    return False
                logger.info('Close login reward via back button')
            else:
                self.device.click_xy(5, 420)
                logger.info(f'Click {point2str(5, 420)} @ LOGIN_REWARD_CLOSE')

            close_timer.reset()
            self._login_reward_confirm_timer = None
            return True

        self._login_reward_confirm_timer = None

        # 仅限制领取文字的 OCR 频率，不阻挡领取后的完成提示检测。
        current_time = time.time()
        if hasattr(self, '_last_login_reward_check') and current_time - self._last_login_reward_check < 5:
            return False
        self._last_login_reward_check = current_time

        reward = self.appear_text(Langs.CLAIM_ALL) or self.appear_text(Langs.CLAIM_REWARD)
        if not reward:
            return False

        self._last_login_reward_check = 0
        x, y = reward[0], reward[1]
        logger.info(f'Click {point2str(x, y)} @ {Langs.CLAIM_ALL}/{Langs.CLAIM_REWARD}')
        self.device.click_xy(x, y)
        self._login_reward_confirm_timer = Timer(2).start()
        return True

    # 阿尼斯的补给品，仅关闭窗口，不抽取
    def handle_anis_supplies(self):
        if self.appear(ANIS_SUPPLIES_CHECK, offset=(30, 30)) and self.appear_then_click(
            ANIS_SUPPLIES_CLOSE, offset=(30, 30), interval=3
        ):
            return True

        return False

    # 屑芙蒂的补给品，仅关闭窗口，不抽取
    def handle_shifty_supplies(self):
        if self.appear(SHIFTY_SUPPLIES_CHECK, offset=(30, 30)) and self.appear_then_click(
            SHIFTY_SUPPLIES_CLOSE, offset=(30, 30), interval=3
        ):
            return True

        return False

    # 露菲扭蛋，抽三次
    def handle_rupee_eggs(self):
        if self.appear(RUPEE_EGG_CHECK, offset=30):
            if self.appear(RUPEE_EGG_DONE, offset=30) and self.appear_then_click(
                RUPEE_EGG_CLOSE, offset=30, interval=1
            ):
                return True
            if self.appear_then_click(RUPEE_EGG_PUSH, offset=30, interval=3):
                return True

        return False

    # 伏特骰子
    def handle_volt_dice(self):
        dice_cooldown = Timer(3)
        if self.appear(VOLT_DICE_CHECK, offset=30):
            while 1:
                self.device.screenshot()
                # 结束
                if self.appear(VOLT_DICE_DONE, offset=30):
                    while 1:
                        self.device.screenshot()
                        if not self.appear(VOLT_DICE_CHECK, offset=30):
                            break
                        if self.appear_then_click(VOLT_DICE_CLOSE, offset=30, interval=1):
                            continue
                    return True
                # 收获
                if self.appear_then_click(REWARD, offset=30, interval=1, static=False):
                    continue
                # 点骰子
                if self.appear(VOLT_DICE_CHECK, offset=30):
                    if dice_cooldown.reached():
                        self.device.click_xy(360, 1050)
                        dice_cooldown.reset()
                    continue

        return False

    def handle_volt_dice_just_reward(self):
        if self.appear(VOLT_DICE_CHECK, offset=30) and self.appear_then_click(VOLT_DICE_CLOSE, offset=30, interval=1):
            return True
        return False

    def handle_reward(self, interval=5):
        if self.appear_then_click(REWARD, offset=(30, 30), interval=interval, static=False):
            return True

    def handle_level_up(self):
        if self.appear(LEVEL_UP_CHECK, offset=(30, 30)):
            self.device.click_xy(360, 920)
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
        if ANNOUNCEMENT_CHECK.match_with_scale(
            self.device.image, threshold=0.85, scale_range=(0.75, 1.05)
        ) and self.appear_then_click(ANNOUNCEMENT, offset=(30, 30), interval=3, threshold=0.85, static=False):
            return True

    def handle_unknown_popup(self):
        # POPUP_CONFIRM 使用勾选图标，兼容通知弹窗的无阴影文字样式。
        return self.appear_then_click_any(
            [POPUP_CONFIRM, CONFIRM_A, CONFIRM_B, CONFIRM_C], offset=(30, 30), interval=3, static=False
        )

    def handle_event_popup(self):
        if self.appear(EVENT_POPUP_CHECK, offset=(30, 30), static=False) and self.appear_then_click(
            EVENT_POPUP_CLOSE, offset=(30, 30), interval=3, static=False
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

    def detect_system_error(self):
        for check in [SYSTEM_ERROR_CHECK, PRODUCT_INFO_ERROR_CHECK, SERVER_DISCONNECTED_CHECK]:
            if self.appear(check, offset=(30, 30), static=False):
                return check

    def handle_system_error(self):
        check = self.detect_system_error()
        if check:
            raise GameSystemError(f'Detected {check.name}')

    @staticmethod
    def handle_stuck(device):
        """卡死计时前三分钟，每分钟尝试恢复一次并返回原任务。"""
        minute = int(device.stuck_timer.current() // 60)
        if minute <= device.stuck_check_minute or minute > STUCK_RECOVERY_MINUTES:
            return False

        device.stuck_check_minute = minute
        device.stuck_recovery_active = True
        try:
            handler = InfoHandler(device.config, device=device)
            check = handler.detect_system_error()
            if check:
                logger.warning(f'Recovering {check.name} at stuck minute {minute}')
                handler.ensure_system_error_closed()
                device.stuck_recovery_handled = True
                return True

            handled = bool(handler.ui_additional(check_system_error=False))
            if handled:
                device.stuck_recovery_handled = True
                logger.info(f'Stuck recovery handled current screen at minute {minute}')
            return handled
        finally:
            device.stuck_recovery_active = False

    def ui_additional(self, check_system_error=True):
        """处理任务等待期间出现的奖励、公告和登录等界面。"""
        if self.handle_level_up():
            return True

        if self.handle_reward():
            return True

        if self.handle_paid_gift():
            return True

        if self.handle_announcement():
            return True

        if self.handle_login_reward():
            return True

        if self.handle_server():
            raise GameStart

        if self.handle_download():
            raise GameStart

        if check_system_error and self.handle_system_error():
            return True

        if self.handle_system_maintenance():
            return True

        if self.appear(LOGIN_PAGE_CHECK, offset=(30, 30)):
            raise GameStart

        if self.appear(LOGO_PAGE_CHECK, offset=(30, 30)):
            raise GameStart

        if self.handle_login():
            return True

        if self.handle_unknown_popup():
            return True

        return False

    def ensure_system_error_closed(self):
        """立即关闭错误弹窗，最多等待 60 秒返回主页或登录页。"""
        if not self.device.stuck_recovery_active:
            self.device.stuck_record_clear()
        self.device.click_record_clear()
        recovery_timeout = Timer(60, count=60).start()

        while 1:
            # 恢复过程有独立超时，恢复中的截图不再触发嵌套卡死检查。
            if not self.device.stuck_recovery_active:
                self.device.stuck_record_clear()
            self.device.screenshot()

            if recovery_timeout.reached():
                raise GameStuckError('System error recovery timed out waiting for main or login page')

            if self.detect_system_error():
                self.handle_unknown_popup()
                continue

            # 弹窗下的主页仍可能匹配模板，需同时检查颜色，确认遮罩已经消失。
            returned_to_main = self.appear(MAIN_CHECK, offset=30) and self.appear(MAIN_CHECK, threshold=10)
            returned_to_login = self.appear_any(
                [LOGIN_PAGE_CHECK, LOGO_PAGE_CHECK, LOGIN_CHECK, LOGIN_CHECK_B], offset=(30, 30)
            )
            if returned_to_main or returned_to_login:
                if not self.device.stuck_recovery_active:
                    self.device.stuck_record_clear()
                self.device.click_record_clear()
                logger.info('System error recovery completed')
                return

    def handle_system_maintenance(self):
        if self.appear(SYSTEM_MAINTENANCE_CHECK, offset=(30, 30), static=False):
            raise GameServerUnderMaintenance('Server is currently under maintenance')

    # def handle_event(self, interval=3):
    #     if self.appear_then_click(SKIP, offset=(5, 5), static=False, interval=interval):
    #         return True
    #     elif self.appear_then_click(
    #             TOUCH_TO_CONTINUE, offset=(5, 5), static=False, interval=interval
    #     ):
    #         self.device.click_xy(360, 720)
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
                self.device.long_click_xy(x_click, y_click, 1)

                # 画面回正
                self.device.sleep(0.5)
                return True

            continue

        return False

    def handle_login_lipass(self):
        if self.appear(LIPASS_CHECK, offset=(30, 30)):
            logger.info('Login with LiPass')
            account, password = load_account(self.config.config_name)
            if not account or not password:
                raise AccountError

            # 输入账号
            if not self.appear(LIPASS_NEXT, offset=(30, 30), threshold=20) and self.appear_then_click(
                LIPASS_ACCOUNT_INPUT, offset=(30, 30), interval=1
            ):
                logger.info('Input LiPass account')
                self.device.sleep(0.3)
                self.device.adb_shell_input_text(account)

                while 1:
                    self.device.screenshot()

                    if not self.appear(LIPASS_ACCOUNT_INPUT, offset=(30, 30)) and self.appear_then_click(
                        LIPASS_CHECK, offset=(30, 30), interval=1
                    ):
                        logger.info('Input LiPass account done')
                        break

            # 下一步
            if self.appear(LIPASS_CHECK, offset=(30, 30)) and self.appear(LIPASS_NEXT, threshold=20):
                while 1:
                    self.device.screenshot()
                    if self.appear_then_click(LIPASS_NEXT, threshold=20, interval=1):
                        continue
                    if self.appear(LIPASS_PASSWORD_CHECK, offset=(30, 30)):
                        break

            # 输入密码
            if self.appear_then_click(LIPASS_PASSWORD_INPUT, offset=(30, 30), interval=1):
                logger.info('Input LiPass password')
                self.device.sleep(0.3)
                self.device.adb_shell_input_text(password)
                next_clicked_at = None

                while 1:
                    self.device.screenshot()

                    # LIPASS 登录验证码，需要人工接管
                    if self.appear(LIPASS_CAPTCHA, offset=30):
                        logger.critical('LiPass captcha detected, request human takeover')
                        raise CaptchaRequired()

                    if self.appear_any(
                        [LIPASS_CHECK, LOGIN_CHECK, LOGIN_CHECK_B, LOGO_PAGE_CHECK],
                        offset=(30, 30),
                    ):
                        break
                    if self.appear(LIPASS_PASSWORD_CHECK, offset=(30, 30)) and self.appear_then_click(
                        LIPASS_NEXT, threshold=20, interval=3
                    ):
                        next_clicked_at = time.monotonic()
                        continue
                    # 仅在 LIPASS_NEXT 尚未就绪且非刚提交后的 5s 等待期时收起软键盘，避免交替连点。
                    if (
                        (next_clicked_at is None or time.monotonic() - next_clicked_at >= 5)
                        and not self.appear(LIPASS_PASSWORD_INPUT, offset=(30, 30))
                        and not self.appear(LIPASS_NEXT, threshold=20)
                        and self.appear_then_click(LIPASS_PASSWORD_CHECK, offset=(30, 30), interval=3)
                    ):
                        logger.info('Confirm LiPass password input')
                        continue
            return True

        return False
