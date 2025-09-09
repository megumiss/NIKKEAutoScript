from module.device.adb.method.uiautomator_2 import Uiautomator2
from module.exception import RequestHumanTakeover
from module.logger import logger


class AppControl(Uiautomator2):
    _app_u2_family = ['minitouch']

    def app_is_running(self) -> bool:
        method = self.config.Emulator_ControlMethod
        if method in AppControl._app_u2_family:
            package = self.app_current_uiautomator2()
        else:
            raise RequestHumanTakeover

        package = package.strip(' \t\r\n')
        logger.attr('Package_name', package)
        return package == self.package

    def app_start(self):
        method = self.config.Emulator_ControlMethod
        logger.info(f'App start: {self.package}')
        if method in AppControl._app_u2_family:
            self.app_start_uiautomator2()
        else:
            raise RequestHumanTakeover

    def app_stop(self):
        method = self.config.Emulator_ControlMethod
        logger.info(f'App stop: {self.package}')
        if method in AppControl._app_u2_family:
            self.app_stop_uiautomator2()
        else:
            raise RequestHumanTakeover
