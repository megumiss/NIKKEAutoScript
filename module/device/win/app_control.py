import os
import re
import time

import psutil

from module.base.langs import Langs
from module.config.config import NikkeConfig
from module.config.language import set_language
from module.config.server import set_server
from module.device.win.game_control import WinClient, Window
from module.device.win.login import Login
from module.exception import AccountError, RequestHumanTakeover
from module.logger import logger

GAME_TITLE = {
    'intl': 'NIKKE',
    'hmt': '勝利女神：妮姬',
}
GAME_PROCESS = {
    'intl': 'nikke.exe',
    'hmt': 'nikke.exe',
}
LAUNCHER_TITLE = {
    'intl': 'NIKKE',
    'hmt': 'NIKKE',
}
LAUNCHER_PROCESS = {
    'intl': 'nikke_launcher.exe',
    'hmt': 'nikke_launcher_hmt.exe',
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
        if not self.config.PCClientInfo_LauncherPath:
            logger.error('Launcher path must be specified')
            raise RequestHumanTakeover
        launcher_path = os.path.normpath(self.config.PCClientInfo_LauncherPath)

        #  英文路径
        if not re.match(r'^[A-Za-z0-9_:/\\.\- ()]+$', launcher_path):
            logger.error(
                f'Please install the game in an English path and enter the correct path, '
                f'current path: [{launcher_path}]'
            )
            raise RequestHumanTakeover

        # 启动器存在
        if not os.path.isfile(launcher_path):
            logger.error(f'Launcher file does not exist or is not a valid file: [{launcher_path}]')
            raise RequestHumanTakeover

        if self.config.PCClientInfo_AutoFillName:
            # 使用固定的 GAME_ / LAUNCHER_ 信息
            launcher_process = LAUNCHER_PROCESS[self.config.PCClientInfo_Client]
            launcher_window_title = LAUNCHER_TITLE[self.config.PCClientInfo_Client]
            game_process = GAME_PROCESS[self.config.PCClientInfo_Client]
            game_window_title = GAME_TITLE[self.config.PCClientInfo_Client]
        else:
            # 使用配置中自定义值
            launcher_process = (
                self.config.PCClientInfo_LauncherProcessName or LAUNCHER_PROCESS[self.config.PCClientInfo_Client]
            )
            launcher_window_title = (
                self.config.PCClientInfo_LauncherTitleName or LAUNCHER_TITLE[self.config.PCClientInfo_Client]
            )
            game_process = self.config.PCClientInfo_GameProcessName or GAME_PROCESS[self.config.PCClientInfo_Client]
            game_window_title = self.config.PCClientInfo_GameTitleName or GAME_TITLE[self.config.PCClientInfo_Client]

        launcher_window_class = 'TWINCONTROL'
        game_window_class = 'UnityWndClass'

        # 创建 Window 对象
        self.launcher = Window(
            name='Launcher',
            title=launcher_window_title,
            class_name=launcher_window_class,
            process=launcher_process,
            path=launcher_path,
        )
        launcher_dir = os.path.dirname(launcher_path)
        self.game = Window(
            name='Game',
            title=game_window_title,
            class_name=game_window_class,
            process=game_process,
            path=self.config.PCClientInfo_GamePath
            or os.path.normpath(os.path.join(launcher_dir, '..', 'NIKKE', 'game', game_process)),
        )

        # 回填配置
        self.config.PCClientInfo_LauncherProcessName = launcher_process
        self.config.PCClientInfo_LauncherTitleName = launcher_window_title
        self.config.PCClientInfo_GameProcessName = game_process
        self.config.PCClientInfo_GameTitleName = game_window_title
        self.config.PCClientInfo_GamePath = self.game.path

        self.interval_timer = {}

        # self.script_path = (
        #     os.path.normpath(script_path)
        #     if script_path and isinstance(script_path, (str, bytes, os.PathLike))
        #     else None
        # )

        # 设置屏幕方向
        if self.config.PCClient_ScreenRotate:
            self.screen_rotate(self.config.PCClient_ScreenNumber, 1)
            time.sleep(3)

        self.language = self.config.Client_Language
        Langs.use(self.language)

        # 启动流程
        self.app_start()
        logger.attr('Process', self.current_window.process)

        # Package
        self.package = self.config.PCClientInfo_Client
        set_server(self.package)
        logger.attr('Client', self.package)
        set_language(self.language)
        logger.attr('Language', self.language)

    def app_is_running(self) -> bool:
        return self.switch_to_program()

    def get_process_path(name):
        # 通过进程名获取运行路径
        for proc in psutil.process_iter(attrs=['pid', 'name']):
            if name in proc.info['name']:
                process = psutil.Process(proc.info['pid'])
                return process.exe()
        return None

    def app_start(self):
        logger.info('Game starting')
        MAX_RETRY = 3

        def wait_until(condition, timeout, period=1):
            """等待直到条件满足或超时"""
            end_time = time.time() + timeout
            while time.time() < end_time:
                if condition():
                    return True
                time.sleep(period)
            return False

        # 检查屏幕分辨率
        # if not self.config.PCClient_ScreenNumber:
        self.check_screen_resolution(self.config.PCClient_ScreenNumber, 720, 1280)
        self.launcher_running = False
        self.current_window = self.game
        # 关闭自动HDR
        if self.config.PCClient_CloseAutoHdr:
            self.change_auto_hdr('disable')
        else:
            self.change_auto_hdr('unset')
        for retry in range(MAX_RETRY):
            try:
                # 检查是否已进入游戏
                if self.switch_to_program():
                    logger.info('Game is already running, verifying resolution')
                    self.ensure_resolution(
                        self.config.PCClient_ScreenNumber, 720, 1280, self.config.PCClient_GameWindowPosition
                    )
                    self.check_resolution(720, 1280)
                    break

                # 启动启动器
                self.current_window = self.launcher
                if not self.switch_to_program() and not self.start_program():
                    logger.error('Launcher failed to start')
                    raise RequestHumanTakeover
                # 切换到启动器前台
                if not wait_until(lambda: self.switch_to_program(), 30):
                    logger.error('Timeout while switching to launcher')
                    raise RequestHumanTakeover
                # 设置启动器分辨率
                # self.ensure_resolution(PROGRAM_LAUNCHER, 900, 600)
                # self.check_resolution(PROGRAM_LAUNCHER, 900, 600)
                # time.sleep(5)

                # 登录
                self.login()
                # 切换到游戏前台
                if not wait_until(lambda: self.switch_to_program(), 60):
                    logger.error('Timeout while switching to game')
                    raise RequestHumanTakeover
                # 设置游戏分辨率
                self.ensure_resolution(
                    self.config.PCClient_ScreenNumber, 720, 1280, self.config.PCClient_GameWindowPosition
                )
                self.check_resolution(720, 1280)

                break
            except AccountError:
                # 直接退出
                raise AccountError
            except Exception as e:
                logger.error(f'Startup error: {e}, retrying {retry + 1}/{MAX_RETRY}')
                self.current_window = self.game
                self.stop_program()
                # 启动器打开失败
                if not self.launcher_running:
                    self.current_window = self.launcher
                    self.stop_program()
                time.sleep(5)
                if retry == MAX_RETRY - 1:
                    raise

        logger.info('Game started')

        # if not wait_until(lambda: screen.get_current_screen(), 360):
        #     raise TimeoutError("获取当前界面超时")

    def app_stop(self, program='Game'):
        try:
            # 关闭游戏或者启动器
            if program == 'Launcher':
                self.current_window = self.launcher
            else:
                self.current_window = self.game

            logger.info(f'{program} stop: {self.current_window.path}')
            if self.stop_program():
                logger.info(f'{program} stop success')
            else:
                logger.warning(f'{program} path config error')
                raise RequestHumanTakeover
        except Exception as e:
            logger.exception(e)
            raise RequestHumanTakeover
