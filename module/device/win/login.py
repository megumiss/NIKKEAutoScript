import ctypes
import time

from module.base.timer import Timer
from module.config.account import load_account
from module.device.win.automation import Automation
from module.device.win.ocr import LauncherOcr
from module.exception import AccountError, RequestHumanTakeover
from module.logger import logger

user32 = ctypes.windll.user32


class Login(LauncherOcr, Automation):
    def login(self):
        logger.info('开始登录')

        confirm_timer = Timer(30, count=10)
        need_login = False
        while True:
            time.sleep(3)

            try:
                self.get_resolution()
                super().screenshot(retry=False)
            except Exception:
                if self.check_program() and self.switch_to_program():
                    logger.info('启动器打开成功')
                    break
            else:
                self.switch_to_program()
                if self.appear_text('电子邮件账号', threshold=0.9):
                    need_login = True
                    break
                if self.appear_text('启动', threshold=0.9) or self.appear_text('更新', threshold=0.9):
                    break
            finally:
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    logger.error('打开启动器超时，未知错误')
                    raise RequestHumanTakeover

        if need_login:
            account, password = load_account(self.config.config_name)
            if not account or not password:
                logger.error('登录失效，请配置账号密码')
                raise AccountError
            # 判断输入框是否存在邮箱
            text = self.ocr_text()
            # 忘记密码的坐标，取x轴
            pass_loc = self.get_location('忘记密码', text)
            email_loc, email_area = self.check_extra_fields(text, '电子邮件账号', '密码')
            if email_loc:
                # 输入框尾部
                self.click_minitouch(pass_loc[0], (email_area[1] + email_area[3]) / 2)

                logger.info('删除已存在的账户信息')
                time.sleep(0.3)
                # 退格键
                for _ in range(30):
                    self.press_key(key='backspace', wait_time=0.05)
                time.sleep(0.3)

            # 输入邮箱
            if email_loc:
                time.sleep(0.3)
                self.click_minitouch(pass_loc[0], (email_area[1] + email_area[3]) / 2)
                self.auto_type(account)
                logger.info('输入账号完成')
            elif self.appear_text_then_click('电子邮件账号', threshold=0.9, interval=1):
                time.sleep(0.3)
                self.auto_type(account)
                logger.info('输入账号完成')

            # 输入密码
            if self.appear_text_then_click('密码', threshold=0.9, interval=1):
                time.sleep(0.3)
                self.auto_type(password)
                logger.info('输入密码完成')

            # 点击保持登录
            if self.appear_text_then_click('保持登录', threshold=0.9, interval=1):
                time.sleep(0.3)
                logger.info('点击保持登录')

            # 点击登录
            if self.appear_text_then_click('登录', threshold=0.9, interval=1):
                logger.info('点击登录')

        confirm_timer = Timer(60, count=20)
        check_game = False
        while True:
            time.sleep(3)

            try:
                self.get_resolution()
                super().screenshot(retry=False)
            except Exception:
                pass
            else:
                if self.appear_text('账号格式错误', threshold=0.9):
                    logger.error('账号格式错误')
                    raise AccountError
                if self.appear_text('暂未设置密码', threshold=0.9):
                    logger.error('账号配置错误')
                    raise AccountError
                if self.appear_text('密码错误', threshold=0.9):
                    logger.error('密码配置错误')
                    raise AccountError

                if self.appear_text('游戏设置', threshold=0.9):
                    if self.appear_text_then_click('启动', threshold=0.9):
                        logger.info('点击启动')
                        check_game = True
                        continue
                    if self.appear_text_then_click('更新', threshold=0.9):
                        logger.info('点击更新')
                        continue
            finally:
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    logger.error('游戏登录超时，未知错误')
                    raise RequestHumanTakeover

                if check_game:
                    self.current_window = self.game
                    if self.check_program() and self.switch_to_program():
                        self.launcher_running = True
                        logger.info('游戏登录成功')
                        break
                # 没进入游戏回退到 launcher
                self.current_window = self.launcher

    def auto_type(self, text):
        # 切换为英文
        self.switch_to_english()
        for character in text:
            self.secretly_press_key(character, wait_time=0.1)
        time.sleep(1)

    def get_keyboard_lang(self):
        """获取当前输入法语言 ID"""
        hwnd = user32.GetForegroundWindow()
        thread_id = user32.GetWindowThreadProcessId(hwnd, 0)
        klid = user32.GetKeyboardLayout(thread_id)
        # 低16位是语言ID
        return klid & 0xFFFF

    def switch_to_english(self):
        """如果是中文输入法(简体/繁体)，则切换到英文"""
        lang_id = self.get_keyboard_lang()

        if lang_id in (0x0804, 0x0404):  # 简体中文 / 繁体中文
            logger.info('输入法切换到英文')
            # 模拟 Shift
            self.press_key('shift')
