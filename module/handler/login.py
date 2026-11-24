import numpy as np

from module.base.timer import Timer
from module.exception import GameStart, GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.handler.assets import *
from module.logger import logger
from module.ui.assets import *
from module.ui.ui import UI


class LoginHandler(UI):
    def _handle_login_interactions(self, click_timer: Timer) -> bool:
        """按固定优先级尝试处理一次登录期弹窗或交互，命中并消费点击冷却时返回 True。"""
        if not click_timer.reached():
            return False

        handlers = (
            self.handle_announcement,
            lambda: self.handle_reward(interval=2),
            self.handle_server,
            self.handle_download,
            self.handle_downloading,
            self.handle_system_error,
            self.handle_system_maintenance,
            self.handle_login_lipass,
            self.handle_paid_gift,
            self.handle_anis_supplies,
            self.handle_shifty_supplies,
            self.handle_rupee_eggs,
            self.handle_volt_dice_just_reward,
            lambda: self.handle_login_reward(in_login=True),
            self.handle_event_popup,
            self.handle_unknown_popup,
        )
        for handler in handlers:
            if handler():
                click_timer.reset()
                return True
        return False

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
        vd_enabled = self._is_virtual_display_active()
        vd_focus_timer = Timer(15, count=10).start()
        vd_prev_sample = None

        while 1:
            # 检查设备方向
            if not login_success and orientation_timer.reached():
                self.device.get_orientation()
                orientation_timer.reset()

            try:
                self.device.screenshot()
            except GameStart:
                continue

            interacted = False

            # 当 MAIN_CHECK 累计出现6次，并且保持在5秒以上
            if self.appear(MAIN_CHECK, offset=(30, 30)) and self.appear(MAIN_CHECK, threshold=20):
                interacted = True
                if confirm_timer.reached():
                    logger.info('Login to main confirm')
                    break
            else:
                confirm_timer.reset()

            # TOUCH TO CONTINUE
            if self.appear(LOGIN_CHECK, offset=(30, 30)) or self.appear(LOGIN_CHECK_B, offset=(30, 30)):
                interacted = True
                self.device.click(LOGIN_CHECK)
                if not login_success:
                    logger.info('Login success')
                    login_success = True

            if self._handle_login_interactions(click_timer):
                vd_focus_timer.reset()
                vd_prev_sample = None
                continue

            if interacted:
                vd_focus_timer.reset()
                vd_prev_sample = None
                continue

            # 仅虚拟屏模式：周期检测纯黑或像素完全冻结，防止 Android <=13 失焦冻死等到 8 分钟超时。
            if vd_enabled:
                image = getattr(self.device, 'image', None)
                if vd_focus_timer.reached():
                    if self._is_black_frame():
                        self.device.recover_virtual_display_focus(reason='black frame', repin=True)
                        vd_prev_sample = None
                    elif image is not None and image.size > 0:
                        sample = image[::16, ::16]
                        if (
                            vd_prev_sample is not None
                            and sample.shape == vd_prev_sample.shape
                            and np.array_equal(sample, vd_prev_sample)
                        ):
                            self.device.recover_virtual_display_focus(reason='frozen frame', repin=True)
                            vd_prev_sample = None
                        else:
                            vd_prev_sample = sample.copy()
                    vd_focus_timer.reset()
                elif (
                    vd_prev_sample is None
                    and image is not None
                    and image.size > 0
                    and not self._is_black_frame()
                ):
                    vd_prev_sample = image[::16, ::16].copy()

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
