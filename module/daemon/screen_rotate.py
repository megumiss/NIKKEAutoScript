import win32api
import win32con

from module.config.utils import deep_get
from module.logger import logger
from module.ui.ui import UI


class ScreenRotate(UI):
    def __init__(self, config):
        super().__init__(config, independent=True)

    def run(self):
        self.screen_rotate(
            deep_get(self.config.data, keys='PCClient.PCClient.ScreenNumber'),
            deep_get(self.config.data, keys='ScreenRotate.ScreenRotate.Orientation'),
        )

    @staticmethod
    def screen_rotate(screen_n=0, orientation=0):
        """
        设置屏幕方向
        orientation: 0=横屏, 1=竖屏(90), 2=横屏翻转, 3=竖屏(270)
        """
        device = win32api.EnumDisplayDevices(None, screen_n)
        dm = win32api.EnumDisplaySettings(device.DeviceName, win32con.ENUM_CURRENT_SETTINGS)

        # 如果当前方向和目标方向不一样
        if dm.DisplayOrientation != orientation:
            # 如果当前是横屏<->竖屏切换，需要交换分辨率宽高
            if (dm.DisplayOrientation + orientation) % 2 == 1:
                dm.PelsWidth, dm.PelsHeight = dm.PelsHeight, dm.PelsWidth

            dm.DisplayOrientation = orientation
            win32api.ChangeDisplaySettingsEx(device.DeviceName, dm)
            logger.info(f'设置屏幕方向：{orientation}')


if __name__ == '__main__':
    b = ScreenRotate('nkas', task='ScreenRotate')
    b.run()
