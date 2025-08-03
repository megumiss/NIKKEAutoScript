from module.base.timer import Timer
from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.handler.assets import *
from module.logger import logger
from module.ui.assets import *
from module.ui.ui import UI


class LoginHandler(UI):
    def _handle_app_login(self):
        """
        Pages:
            in: Any page
            out: page_main
        """
        logger.hr('App login')

        confirm_timer = Timer(5, count=6).start()
        click_timer = Timer(0.3)
        orientation_timer = Timer(10)
        login_success = False

        while 1:
            # 检查设备方向
            if not login_success and orientation_timer.reached():
                self.device.get_orientation()
                orientation_timer.reset()

            self.device.screenshot()

            # 当 MAIN_CHECK 累计出现6次，并且保持在5秒以上
            if self.appear(MAIN_CHECK, offset=(30, 30)):
                if confirm_timer.reached():
                    logger.info('Login to main confirm')
                    break
            else:
                confirm_timer.reset()

            if self.appear_text('将下载', interval=3):
                self.appear_text_then_click('确认', interval=3)
                continue

            if self.appear_text('正在下载游戏执行所需', interval=3):
                self.device.stuck_record_clear()
                self.device.click_record_clear()
                self.device.sleep(20)
                continue

            # TOUCH TO CONTINUE
            if self.appear(LOGIN_CHECK, offset=(30, 30), interval=5) or self.appear(
                LOGIN_CHECK_B, offset=(30, 30), interval=5
            ):
                self.device.click(LOGIN_CHECK)
                if not login_success:
                    logger.info('Login success')
                    login_success = True

            # 公告
            if click_timer.reached() and self.handle_announcement():
                click_timer.reset()
                continue

            # ———— REWARD ————
            if click_timer.reached() and self.handle_reward(interval=2):
                click_timer.reset()
                continue

            if click_timer.reached() and self.handle_server():
                click_timer.reset()
                continue

            if click_timer.reached() and self.handle_download():
                click_timer.reset()
                continue

            if click_timer.reached() and self.handle_system_error():
                click_timer.reset()
                continue

            if click_timer.reached() and self.handle_system_maintenance():
                click_timer.reset()
                continue

            # 礼包
            if click_timer.reached() and self.handle_paid_gift():
                click_timer.reset()
                continue

            # Daily Login, Memories Spring, Monthly Card, etc.
            if click_timer.reached() and self.handle_login_reward():
                click_timer.reset()
                continue

            # 屑芙蒂5x5补给品
            if click_timer.reached() and self.handle_shifty_supplies():
                click_timer.reset()
                continue

            # 回到主页
            if click_timer.reached() and self.appear_then_click(GOTO_MAIN, offset=(30, 30), interval=5):
                click_timer.reset()
                continue

    def handle_app_login(self) -> bool:
        """
        Returns:
            bool: 是否登录成功

        Raises:
            RequestHumanTakeover: 当登录失败次数大于3次时
        """
        for _ in range(3):
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            try:
                self._handle_app_login()
                return True
            except (GameTooManyClickError, GameStuckError) as e:
                logger.warning(e)
                self.device.app_stop()
                self.device.app_start()
                continue

        logger.critical('Login failed more than 3')
        logger.critical('NIKKE server may be under maintenance, or you may lost network connection')
        raise RequestHumanTakeover

    def app_start(self):
        logger.hr('App start')
        self.device.app_start()
        self.handle_app_login()

    def app_stop(self):
        logger.hr('App stop')
        self.device.app_stop()

    def app_restart(self):
        logger.hr('App restart')
        self.device.app_stop()
        self.device.app_start()
        self.handle_app_login()
        self.config.task_delay(server_update=True)
