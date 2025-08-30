import os
import time
from functools import cached_property

from module.config.config import NikkeConfig
from module.config.deep import deep_iter
from module.device.win.game_control import WinClient
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.config.language import set_language
from module.config.server import VALID_CHANNEL_PACKAGE, VALID_PACKAGE, set_server


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
       
        self.game_path = os.path.normpath(self.config.WinClient_Path)
        self.process_name = self.config.WinClient_ProcessName
        self.window_name = self.config.WinClient_TitleName
        self.window_class = "UnityWndClass"
        # self.script_path = (
        #     os.path.normpath(script_path)
        #     if script_path and isinstance(script_path, (str, bytes, os.PathLike))
        #     else None
        # )
        
        self.app_start()
        logger.attr('AdbDevice', self.process_name)

        # Package
        self.package = self.config.Emulator_PackageName
        set_server(self.package)
        logger.attr('PackageName', self.package)
        self.language = self.config.Emulator_Language
        set_language(self.language)
        logger.attr('Language', self.language)

    def app_is_running(self) -> bool:
        if not self.win_client.switch_to_game():
            return False
        
        return True
        
    def app_start(self):
        logger.info(f'Game start: {self.config.WinClient_Path}')
        MAX_RETRY = 3
        
        for retry in range(MAX_RETRY):
            try:
                if not self.switch_to_game():
                    # self.win_client.change_auto_hdr("disable")

                    if not self.start_game():
                        raise Exception("Start game failed")
                    time.sleep(5)

                #     if not wait_until(lambda: self.win_client.switch_to_game(), 60):
                #         self.win_client.restore_resolution()
                #         # self.win_client.restore_auto_hdr()
                #         raise TimeoutError("Switch to game window timeout")

                #     time.sleep(5)
                #     self.win_client.restore_resolution()
                #     # self.win_client.restore_auto_hdr()
                #     self.win_client.check_resolution_ratio(720, 1280)

                #     # if not wait_until(lambda: check_and_click_enter(), 600):
                #     #     raise TimeoutError("Failed to click Enter button")
                    
                #     time.sleep(5)
                else:
                    self.check_resolution(720, 1280)
                    time.sleep(1)
                    self.change_resolution(720, 1280)
                    time.sleep(1)
                    self.check_resolution_ratio(720, 1280)
                    time.sleep(1)
                #     if cfg.auto_set_game_path_enable:
                #         program_path = get_process_path(cfg.game_process_name)
                #         if program_path is not None and program_path != cfg.game_path:
                #             cfg.set_value("game_path", program_path)
                #             logger.info(f"游戏路径更新成功：{program_path}")
                #     time.sleep(1)

                # if not wait_until(lambda: screen.get_current_screen(), 360):
                #     raise TimeoutError("获取当前界面超时")
                # break  # 成功启动游戏，跳出重试循环
            except Exception as e:
                logger.error(f"尝试启动游戏时发生错误：{e}")
                self.stop_game()  # 确保在重试前停止游戏
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
            if self.win_client.stop_game():
                logger.info('Game stop success')
            else:
                logger.warning('Game path config error')
                raise RequestHumanTakeover
        except Exception:
            raise RequestHumanTakeover