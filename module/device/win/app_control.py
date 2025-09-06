import os
import time

import cv2
import numpy as np
import psutil

from module.config.config import NikkeConfig
from module.config.language import set_language
from module.config.server import set_server
from module.device.win.automation import Window
from module.device.win.game_control import WinClient
from module.device.win.login import Login
from module.exception import RequestHumanTakeover
from module.logger import logger

GAME_TITLE = {
    'intl': 'NIKKE',
    'hwt': '勝利女神：妮姬',
}
GAME_PROCESS = {
    'intl': 'nikke.exe',
    'hwt': 'nikke_hmt.exe',
}
LAUNCHER_TITLE = {
    'intl': 'NIKKE',
    'hwt': 'NIKKE',
}
LAUNCHER_PROCESS = {
    'intl': 'nikke_launcher.exe',
    'hwt': 'nikke_launcher_hmt.exe',
}


class AppControl(WinClient, Login):
    config: NikkeConfig

    def __init__(self, config):
        """
        Args:
            config (NikkeConfig, str): Name of the user config under ./config
        """
        logger.hr('Device', level=1)
        if isinstance(config, str):
            self.config = NikkeConfig(config, task=None)
        else:
            self.config = config
        super().__init__(config)

        # 启动器信息
        launcher_path = os.path.normpath(self.config.PCClientInfo_LauncherPath)
        launcher_process = (
            self.config.PCClientInfo_LauncherProcessName or LAUNCHER_PROCESS[self.config.PCClientInfo_Client]
        )
        launcher_window_title = (
            self.config.PCClientInfo_LauncherTitleName or LAUNCHER_TITLE[self.config.PCClientInfo_Client]
        )
        launcher_window_class = 'TWINCONTROL'

        # 游戏信息
        # game_path = os.path.normpath(self.config.PCClientInfo_GamePath)
        game_process = self.config.PCClientInfo_GameProcessName or GAME_PROCESS[self.config.PCClientInfo_Client]
        game_window_title = self.config.PCClientInfo_GameTitleName or GAME_TITLE[self.config.PCClientInfo_Client]
        game_window_class = 'UnityWndClass'

        # 创建 Window 对象
        self.launcher = Window(
            name='Game',
            title=launcher_window_title,
            class_name=launcher_window_class,
            process=launcher_process,
            path=launcher_path,
        )
        self.game = Window(
            name='Launcher',
            title=game_window_title,
            class_name=game_window_class,
            process=game_process,
            path='',
        )

        # 回填配置
        self.config.PCClientInfo_LauncherProcessName = launcher_process
        self.config.PCClientInfo_LauncherTitleName = launcher_window_title
        self.config.PCClientInfo_GameProcessName = game_process
        self.config.PCClientInfo_GameTitleName = game_window_title

        self.interval_timer = {}

        # self.script_path = (
        #     os.path.normpath(script_path)
        #     if script_path and isinstance(script_path, (str, bytes, os.PathLike))
        #     else None
        # )

        # 启动流程
        self.app_start()
        logger.attr('WinClient', self.game.process)

        # Package
        self.package = self.config.Emulator_PackageName
        set_server(self.package)
        logger.attr('PackageName', self.package)
        self.language = self.config.Emulator_Language
        set_language(self.language)
        logger.attr('Language', self.language)

    def app_is_running(self) -> bool:
        return self.game.switch_to_foreground()

    def get_process_path(name):
        # 通过进程名获取运行路径
        for proc in psutil.process_iter(attrs=['pid', 'name']):
            if name in proc.info['name']:
                process = psutil.Process(proc.info['pid'])
                return process.exe()
        return None

    def app_start(self):
        logger.info(f'Game start: {self.game.path}')
        MAX_RETRY = 3

        def wait_until(condition, timeout, period=1):
            """等待直到条件满足或超时"""
            end_time = time.time() + timeout
            while time.time() < end_time:
                if condition():
                    return True
                time.sleep(period)
            return False

        self.current_window = self.game
        for retry in range(MAX_RETRY):
            try:
                # 检查屏幕分辨率
                self.check_screen_resolution(720, 1280)
                # 检查是否已进入游戏
                if self.game.switch_to_foreground():
                    logger.info('游戏已在运行，检查分辨率')
                    self.ensure_resolution(720, 1280)
                    self.check_resolution(720, 1280)
                    break

                # 启动启动器
                self.current_window = self.launcher
                if not self.launcher.start():
                    logger.error('启动器启动失败')
                    raise RequestHumanTakeover
                # 切换到启动器前台
                if not wait_until(lambda: self.current_window.switch_to_foreground(), 30):
                    logger.error('切换到启动器超时')
                    raise RequestHumanTakeover
                # 设置启动器分辨率
                # self.ensure_resolution(PROGRAM_LAUNCHER, 900, 600)
                # self.check_resolution(PROGRAM_LAUNCHER, 900, 600)
                # time.sleep(5)

                # 登录
                self.app_login()
                time.sleep(5)

                # 切换到游戏前台
                if not wait_until(lambda: self.current_window.switch_to_foreground(), 60):
                    logger.error('切换到游戏超时')
                    raise RequestHumanTakeover
                # 设置游戏分辨率
                self.ensure_resolution(720, 1280)
                self.check_resolution(720, 1280)

                break
            except Exception as e:
                logger.error(f'尝试启动流程时发生错误：{e}')
                self.game.stop()
                self.launcher.stop()
                time.sleep(5)
                if retry == MAX_RETRY - 1:
                    raise

        logger.info('Game started')

        # if not wait_until(lambda: screen.get_current_screen(), 360):
        #     raise TimeoutError("获取当前界面超时")

    def app_stop(self):
        logger.info(f'Game stop: {self.game.path}')

        try:
            if self.game.stop():
                logger.info('Game stop success')
            else:
                logger.warning('Game path config error')
                raise RequestHumanTakeover
        except Exception as e:
            logger.exception(e)
            raise RequestHumanTakeover

    def app_login(self):
        """检查窗口状态并登录"""
        try:
            self.login()
        except Exception as e:
            logger.exception(e)
            raise RequestHumanTakeover

        # 界面刷新，再次设置启动器分辨率
        # self.ensure_resolution(900, 600)
        # self.check_resolution(900, 600)
