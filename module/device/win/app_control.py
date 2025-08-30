import os
import time

import psutil

from module.config.config import NikkeConfig
from module.config.language import set_language
from module.config.server import set_server
from module.device.win.game_control import WinClient
from module.exception import RequestHumanTakeover
from module.logger import logger


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

        self.game_path = os.path.normpath(self.config.WinClient_Path)
        self.process_name = self.config.WinClient_ProcessName
        self.window_name = self.config.WinClient_TitleName
        self.window_class = 'UnityWndClass'
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
        if not self.switch_to_game():
            return False

        return True

    def get_process_path(name):
        # 通过进程名获取运行路径
        for proc in psutil.process_iter(attrs=['pid', 'name']):
            if name in proc.info['name']:
                process = psutil.Process(proc.info['pid'])
                return process.exe()
        return None

    def app_start(self):
        logger.info(f'Game start: {self.config.WinClient_Path}')
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
                if not self.switch_to_game():
                    # self.change_auto_hdr("disable")

                    # TODO 自动启动游戏
                    # if not self.start_game():
                    #     raise Exception('Start game failed')
                    # time.sleep(5)

                    if not wait_until(lambda: self.switch_to_game(), 60):
                        # self.restore_auto_hdr()
                        raise TimeoutError('切换到游戏超时')

                    # time.sleep(2)
                    # self.restore_auto_hdr()
                else:
                    self.check_resolution(720, 1280)
                    time.sleep(1)
                    self.change_resolution(720, 1280)
                    time.sleep(1)
                    self.check_resolution_ratio(720, 1280)
                    time.sleep(1)
                
                # TODO 自动更新游戏路径
                #     if cfg.auto_set_game_path_enable:
                #         program_path = get_process_path(cfg.game_process_name)
                #         if program_path is not None and program_path != cfg.game_path:
                #             cfg.set_value("game_path", program_path)
                #             logger.info(f"游戏路径更新成功：{program_path}")
                #     time.sleep(1)

                # if not wait_until(lambda: screen.get_current_screen(), 360):
                #     raise TimeoutError("获取当前界面超时")
                break
            except Exception as e:
                logger.error(f'尝试启动游戏时发生错误：{e}')
                self.stop_game()
                if retry == MAX_RETRY - 1:
                    raise

        #     if game.start_game():
        #         logger.info('Game start success')
        #     else:
        #         logger.warning('Game path config error')
        #         raise RequestHumanTakeover
        # except Exception:
        #     raise RequestHumanTakeover

    def app_stop(self):
        logger.info(f'Game stop: {self.config.WinClient_Path}')

        try:
            if self.stop_game():
                logger.info('Game stop success')
            else:
                logger.warning('Game path config error')
                raise RequestHumanTakeover
        except Exception:
            raise RequestHumanTakeover
