import atexit
import re
import time
from collections import deque

from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import publish_preview_frame
from module.device.adb.app_control import AppControl
from module.device.adb.control import Control
from module.device.adb.env import IS_WINDOWS
from module.device.adb.screenshot import Screenshot
from module.exception import (
    EmulatorNotRunningError,
    GameNotRunningError,
    GameStuckError,
    GameTooManyClickError,
    RequestHumanTakeover,
)
from module.logger import logger
from module.ocr.models import OCR_MODEL


class Device(Screenshot, Control, AppControl):
    get_location = OCR_MODEL.get_location

    # 尝试检测的 Button 集合
    detect_record = set()
    # 点击过的 Button 队列
    click_record = deque(maxlen=15)
    # 操作计时器
    stuck_timer = Timer(360, count=60).start()
    stuck_timer_long = Timer(480, count=180).start()
    """ 
        如果 detect_record 含有在 stuck_long_wait_list 中的 Button，在 stuck_timer_long 到达上限前不会 raise exception
        detect_record 值为 str(Button)，在 Button 类中，重写为该 asset 的名称
    """
    stuck_long_wait_list = ['LOGIN_CHECK', 'PAUSE']

    def __init__(self, *args, **kwargs):
        for trial in range(4):
            try:
                super().__init__(*args, **kwargs)
                break
            except EmulatorNotRunningError:
                if self.config.PhysicalDevice_Enable:
                    logger.critical(
                        f'Failed to connect to physical device "{self.config.Emulator_Serial}", '
                        f'please check `adb connect` and wireless debugging on the device'
                    )
                    raise RequestHumanTakeover
                if trial >= 3:
                    logger.critical('Failed to start emulator after 3 trial')
                    raise RequestHumanTakeover
                # Try to start emulator
                if self.emulator_instance is not None:
                    self.emulator_start()
                else:
                    logger.critical(
                        f'No emulator with serial "{self.config.Emulator_Serial}" found, '
                        f'please set a correct serial'
                    )
                    raise RequestHumanTakeover

        # Auto-fill emulator info
        if IS_WINDOWS and not self.config.PhysicalDevice_Enable \
                and self.config.EmulatorInfo_Emulator == 'auto':
            _ = self.emulator_instance

        if self.config.PhysicalDevice_Enable:
            if self.config.Emulator_ControlMethod == 'minitouch':
                logger.warning('minitouch is unavailable on physical devices, '
                               'please set Emulator.ControlMethod to uiautomator2 or ADB')
            self._physical_device_resolution_set()

    @staticmethod
    def _wm_override_value(output: str, key: str):
        """wm size/density 输出中存在 Override 行时返回其值，否则 None。"""
        match = re.search(rf'Override {key}: (\S+)', output)
        return match.group(1) if match else None

    def _physical_device_resolution_set(self):
        """
        真机模式：临时把设备分辨率改为模板基准 720x1280。
        AutoRestoreResolution 开启时进程正常退出自动恢复，被强杀时由 webui 侧 ProcessManager.stop 兜底
        （兜底路径拿不到 override 记录，仍是 reset，属异常路径可接受）；关闭时由用户在设置页手动还原。
        只改 wm size 不改 density 会导致 UI 按原 dp 尺寸渲染，画面等比放大（图标出屏），
        density 固定为 240，与模拟器的 720x1280@240dpi 一致。
        """
        size_out = self.adb_shell(['wm', 'size'])
        logger.info(f'Physical device display: {size_out}')
        density_out = self.adb_shell(['wm', 'density'])
        logger.info(f'Physical device density: {density_out}')
        # 记录用户已有的 override，退出时还原具体值；直接 reset 会丢掉用户自己的分辨率/DPI 覆盖
        self._physical_size_override = self._wm_override_value(size_out, 'size')
        self._physical_density_override = self._wm_override_value(density_out, 'density')
        self.adb_shell(['wm', 'size', '720x1280'])
        self.adb_shell(['wm', 'density', '240'])
        # wm size 立即写入但显示管线生效有延迟，等它真正切换完成，避免首帧截图拿到旧分辨率
        time.sleep(2)
        # 读回校验：设备拒绝 wm 命令时 adb_shell 不抛异常，静默继续只会让后续截图/坐标全错
        size_now = self.adb_shell(['wm', 'size'])
        density_now = self.adb_shell(['wm', 'density'])
        if 'Override size: 720x1280' not in size_now or 'Override density: 240' not in density_now:
            logger.critical(
                f'Failed to set physical device resolution to 720x1280@240: '
                f'size={size_now!r}, density={density_now!r}'
            )
            raise RequestHumanTakeover
        if self.config.PhysicalDevice_AutoRestoreResolution:
            atexit.register(self._physical_device_resolution_reset)

    def _physical_device_resolution_reset(self):
        logger.info('Restore physical device resolution')
        size = getattr(self, '_physical_size_override', None) or 'reset'
        density = getattr(self, '_physical_density_override', None) or 'reset'
        try:
            self.adb_shell(['wm', 'size', size])
            self.adb_shell(['wm', 'density', density])
        except Exception as e:
            logger.warning(f'Failed to restore resolution, run `adb shell wm size reset` manually: {e}')

        # TODO
        # self.screenshot_interval_set()

    def screenshot(self):
        """
            截图

            Returns:
                np.ndarray:
        """
        self.stuck_record_check()
        super().screenshot()
        publish_preview_frame(self.image)
        return self.image

    def handle_control_check(self, button: Button):
        """
            当点击(匹配到)Button时，清空尝试匹配过的按钮，重置操作计时器，并记录此Button，再检查点击过的Buttons

            Args:
                button: Button
        """
        self.stuck_record_clear()
        self.click_record_add(button)
        self.click_record_check()

    def click_record_check(self):
        """
            检查点击过的Buttons

            Raises:
                GameTooManyClickError:
        """
        count = {}
        for key in self.click_record:
            """
                当click_record为 ['button','button'] 时
                
                round 1:
                    count['button'] = count.get('button', default=0) + 1
                                        ↓
                    count['button'] = count.get('button', default=0) => 0 + 1
                    
                round 2:                ↓
                    count['button'] = count.get('button', default=0) => 1 + 1
                    
                count[key] = count.get(key, default=0) + 1 添加参数名 'default' 会 raise TypeError: dict.get() takes no keyword arguments
            """
            count[key] = count.get(key, 0) + 1
        count = sorted(count.items(), key=lambda item: item[1])
        if count[0][1] >= 12:
            logger.warning(f'Too many click for a button: {count[0][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            self.click_record_clear()
            raise GameTooManyClickError(f'Too many click for a button: {count[0][0]}')
        if len(count) >= 2 and count[0][1] >= 6 and count[1][1] >= 6:
            logger.warning(f'Too many click between 2 buttons: {count[0][0]}, {count[1][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            self.click_record_clear()
            raise GameTooManyClickError(f'Too many click between 2 buttons: {count[0][0]}, {count[1][0]}')

    def click_record_add(self, button: Button):
        """
            记录点击过的button

            Args:
                button: Button
                str(button): 值默认为asset名称
        """
        self.click_record.append(str(button))

    def click_record_clear(self):
        """
            清空点击过的button
        """
        self.click_record.clear()

    def stuck_record_check(self):
        """
            当操作计时器: stuck_timer，stuck_timer_long 到达限制时间时 raise exception

            如果 detect_record 含有在 stuck_long_wait_list 中的 Button，在 stuck_timer_long 到达上限前不会 raise exception
            detect_record 值为 str(Button)，在 Button 类中，默认重写为该 asset 的名称

            Raises:
                GameStuckError:
        """
        reached = self.stuck_timer.reached()
        reached_long = self.stuck_timer_long.reached()

        if not reached:
            return False
        if not reached_long:
            for button in self.stuck_long_wait_list:
                if button in self.detect_record:
                    return False

        logger.warning('Wait too long')
        logger.warning(f'Waiting for {self.detect_record}')
        self.stuck_record_clear()

        # from module.ui.ui import UI
        # ui = UI(self.config, device=self)
        # if ui.ui_additional():
        #     return False

        if self.app_is_running():
            raise GameStuckError('Wait too long')
        else:
            raise GameNotRunningError('Game died')

    def stuck_record_clear(self):
        """
            清空尝试匹配过的按钮，重置操作计时器
        """
        self.detect_record = set()
        self.stuck_timer.reset()
        self.stuck_timer_long.reset()

    def disable_stuck_detection(self):
        """
            Alas: Disable stuck detection and its handler. Usually uses in semi auto and debugging.
            禁用检查点击，操作计时器，这样在卡住时不会有任何响应
        """
        logger.info('Disable stuck detection')

        def empty_function(*arg, **kwargs):
            return False

        self.click_record_check = empty_function
        self.stuck_record_check = empty_function

    def stuck_record_add(self, button: Button):
        """
            记录尝试匹配(未匹配)的button，click_record_add 为点击过(匹配到)的button

            Args:
                button: Button
        """
        self.detect_record.add(str(button))

    def app_start(self):
        """
            启动NIKKE
        """
        super().app_start()
        self.stuck_record_clear()
        self.click_record_clear()

    def app_stop(self):
        """
            停止NIKKE
        """
        super().app_stop()
        self.stuck_record_clear()
        self.click_record_clear()
