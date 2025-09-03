import os
import time

import cv2
import numpy as np
import psutil
import pyautogui

from module.config.config import NikkeConfig
from module.config.language import set_language
from module.config.server import set_server
from module.device.win.game_control import WinClient
from module.exception import RequestHumanTakeover
from module.logger import logger

PROGRAM_GAME = 'game'
PROGRAM_LAUNCHER = 'launcher'

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


class AppControl(WinClient):
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
        self.launcher_path = os.path.normpath(self.config.PCClientInfo_LauncherPath)
        self.launcher_process_name = (
            self.config.PCClientInfo_LauncherProcessName or LAUNCHER_PROCESS[self.config.PCClientInfo_Client]
        )
        self.launcher_window_name = (
            self.config.PCClientInfo_LauncherTitleName or LAUNCHER_TITLE[self.config.PCClientInfo_Client]
        )
        self.launcher_window_class = 'TWINCONTROL'

        # 游戏信息
        self.game_path = os.path.normpath(self.config.PCClientInfo_GamePath)
        self.game_process_name = (
            self.config.PCClientInfo_GameProcessName or GAME_PROCESS[self.config.PCClientInfo_Client]
        )
        self.game_window_name = (
            self.config.PCClientInfo_GameTitleName or GAME_TITLE[self.config.PCClientInfo_Client]
        )
        self.game_window_class = 'UnityWndClass'
        # self.script_path = (
        #     os.path.normpath(script_path)
        #     if script_path and isinstance(script_path, (str, bytes, os.PathLike))
        #     else None
        # )

        self.app_start()
        logger.attr('WinClient', self.process_name)

        # Package
        self.package = self.config.Emulator_PackageName
        set_server(self.package)
        logger.attr('PackageName', self.package)
        self.language = self.config.Emulator_Language
        set_language(self.language)
        logger.attr('Language', self.language)

    def app_is_running(self) -> bool:
        if not self.switch_to_program(PROGRAM_GAME):
            return False

        return True

    def get_process_path(name):
        # 通过进程名获取运行路径
        for proc in psutil.process_iter(attrs=['pid', 'name']):
            if name in proc.info['name']:
                process = psutil.Process(proc.info['pid'])
                return process.exe()
        return None

    def app_login():
        screenshot = pyautogui.screenshot()
        cv2.imwrite('uac.png', np.array(screenshot))
        
        pass

    def app_start(self):
        logger.info(f'Game start: {self.config.PCClientInfo_GamePath}')
        MAX_RETRY = 3

        def wait_until(condition, timeout, period=1):
            """等待直到条件满足或超时"""
            end_time = time.time() + timeout
            while time.time() < end_time:
                if condition():
                    return True
                time.sleep(period)
            return False

        for retry in range(MAX_RETRY):
            try:
                # 检查是否已进入游戏
                if self.switch_to_program(PROGRAM_GAME):
                    logger.info('游戏已在运行，检查分辨率')
                    self.check_screen_resolution(720, 1280)
                    time.sleep(0.5)
                    if self.config.PCClient_GameResolutionCompat:
                        self.change_resolution_compat(PROGRAM_GAME, 720, 1280)
                    else:
                        self.change_resolution(PROGRAM_GAME, 720, 1280)
                    time.sleep(0.5)
                    self.check_resolution(PROGRAM_GAME, 720, 1280)
                    break

                # 启动启动器
                if not self.start_program(PROGRAM_LAUNCHER):
                    logger.error('启动器启动失败')
                    raise RequestHumanTakeover
                # 切换到启动器前台
                if not wait_until(lambda: self.switch_to_program(PROGRAM_LAUNCHER), 30):
                    logger.error('切换到启动器超时')
                    raise RequestHumanTakeover
                # 设置启动器分辨率
                if self.config.PCClient_LauncherResolutionCompat:
                    self.change_resolution_compat(PROGRAM_LAUNCHER, 900, 600)
                else:
                    self.change_resolution(PROGRAM_LAUNCHER, 900, 600)
                time.sleep(1)
                self.check_resolution(PROGRAM_LAUNCHER, 900, 600)
                time.sleep(0.5)

                # 点击登录按钮
                self.app_login()
                time.sleep(5)

                # 切换到游戏前台
                if not wait_until(lambda: self.switch_to_program(PROGRAM_GAME), 60):
                    logger.error('切换到游戏超时')
                    raise RequestHumanTakeover

                # 设置游戏分辨率
                if self.config.PCClient_ResolutionCompat:
                    self.change_resolution_compat(PROGRAM_GAME, 720, 1280)
                else:
                    self.change_resolution(PROGRAM_GAME, 720, 1280)
                self.check_resolution(PROGRAM_GAME, 720, 1280)

                break
            except Exception as e:
                logger.error(f'尝试启动流程时发生错误：{e}')
                self.stop_program(PROGRAM_GAME)
                self.stop_program(PROGRAM_LAUNCHER)
                time.sleep(5)
                if retry == MAX_RETRY - 1:
                    raise

        logger.info('Game started')

        # TODO 自动更新游戏路径
        #     if cfg.auto_set_game_path_enable:
        #         program_path = get_process_path(cfg.game_process_name)
        #         if program_path is not None and program_path != cfg.game_path:
        #             cfg.set_value("game_path", program_path)
        #             logger.info(f"游戏路径更新成功：{program_path}")
        #     time.sleep(1)

        # if not wait_until(lambda: screen.get_current_screen(), 360):
        #     raise TimeoutError("获取当前界面超时")

    def app_stop(self):
        logger.info(f'Game stop: {self.config.PCClientInfo_GamePath}')

        try:
            if self.stop_game():
                logger.info('Game stop success')
            else:
                logger.warning('Game path config error')
                raise RequestHumanTakeover
        except Exception:
            raise RequestHumanTakeover
