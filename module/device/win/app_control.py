from module.config.config import NikkeConfig
from module.device.win.gamecontroller import GameController
from module.exception import RequestHumanTakeover
from module.logger import logger


class AppControl:
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

    def app_is_running(self) -> bool:
        method = self.config.Emulator_ControlMethod
        if method in AppControl._app_u2_family:
            package = self.app_current_uiautomator2()
        else:
            raise RequestHumanTakeover

        package = package.strip(' \t\r\n')
        logger.attr('Package_name', package)
        return package == self.package

    def app_stop(self):
        logger.info(f'App stop: {self.package}')

        return

    def app_start(self):
        logger.info(f'App start: {self.package}')

        game = GameController(
            self.config.WinClient_Path,
            self.config.WinClient_ProcessName,
            self.config.WinClient_TitleName,
            'UnityWndClass',
        )
        try:
            if game.start_game():
                logger.info('Game start success')
            else:
                logger.warning('Game path config error')
                raise RequestHumanTakeover
        except Exception:
            raise RequestHumanTakeover
