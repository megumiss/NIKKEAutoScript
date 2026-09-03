import atexit
import os
import re
import subprocess
import threading
import time
from collections import deque

from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import publish_preview_frame
from module.device.adb.app_control import AppControl
from module.device.adb.control import Control
from module.device.adb.env import IS_WINDOWS
from module.device.adb.screenshot import Screenshot, ScreenshotSizeError
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
        self._virtual_display_process = None
        self._virtual_display_reader = None
        self._virtual_display_ready = threading.Event()
        self._virtual_display_id = None
        self._virtual_capture_port = None
        self._virtual_socket_name = None
        # The bridge reports the actual ImageReader geometry. Keep the
        # configured 720x1280 logical coordinates separate from raw frames so
        # landscape-native tablets can be normalized without touching ADB.
        self._virtual_display_width = 720
        self._virtual_display_height = 1280
        self._virtual_display_rotation = 0
        self._virtual_display_raw_width = 720
        self._virtual_display_raw_height = 1280
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
            if self.config.PhysicalDevice_VirtualDisplay:
                self._virtual_display_start()
            else:
                if self.config.Emulator_ControlMethod == 'minitouch':
                    logger.warning('minitouch is unavailable on physical devices, '
                                   'please set Emulator.ControlMethod to uiautomator2 or ADB')
                self._physical_device_resolution_set()

    @staticmethod
    def _bridge_display_id(line: str):
        match = re.search(r'NKAS_VD_READY id=(\d+)', line)
        return int(match.group(1)) if match else None

    @staticmethod
    def _bridge_display_info(line: str):
        """Parse the bridge INFO/READY geometry without requiring a device."""
        match = re.search(
            r'(?:NKAS_VD_READY|OK)\s+id=(?P<id>\d+)'
            r'(?:[^\n]*?\s+size=(?P<w>\d+)x(?P<h>\d+))?'
            r'(?:[^\n]*?\s+rotation=(?P<rotation>\d+))?',
            line,
        )
        if not match:
            return None
        return {
            'id': int(match.group('id')),
            'width': int(match.group('w')) if match.group('w') else None,
            'height': int(match.group('h')) if match.group('h') else None,
            'rotation': int(match.group('rotation')) if match.group('rotation') else 0,
        }

    def _read_virtual_display_output(self):
        process = self._virtual_display_process
        if process is None or process.stdout is None:
            self._virtual_display_ready.set()
            return
        try:
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    logger.info(f'Virtual display: {line}')
                display_id = self._bridge_display_id(line)
                if display_id is not None:
                    self._virtual_display_id = display_id
                    info = self._bridge_display_info(line)
                    if info:
                        self._virtual_display_raw_width = info['width'] or self._virtual_display_raw_width
                        self._virtual_display_raw_height = info['height'] or self._virtual_display_raw_height
                        self._virtual_display_rotation = info['rotation']
                    self._virtual_display_ready.set()
        finally:
            process.stdout.close()
            self._virtual_display_ready.set()

    def _refresh_virtual_display_info(self):
        """Refresh bridge geometry after the display has settled on an OEM ROM."""
        try:
            response = self._virtual_display_command(b'INFO\n', timeout=2)
            if isinstance(response, bytes):
                response = response.decode('utf-8', errors='replace')
            info = self._bridge_display_info(response)
            if not info:
                return False
            self._virtual_display_id = info['id']
            if info['width'] and info['height']:
                self._virtual_display_raw_width = info['width']
                self._virtual_display_raw_height = info['height']
            self._virtual_display_rotation = info['rotation']
            logger.debug(
                f'Virtual display geometry: raw={self._virtual_display_raw_width}x'
                f'{self._virtual_display_raw_height}, rotation={self._virtual_display_rotation}'
            )
            return True
        except Exception as e:
            logger.debug(f'Virtual display INFO unavailable: {e}')
            return False

    def _repin_app_to_virtual_display(self):
        """Move an OEM-relocated app task back to the virtual display."""
        try:
            response = self._virtual_display_command(
                f'REPIN {self.package}\n'.encode('utf-8'), timeout=3
            )
            if isinstance(response, bytes) and response.startswith(b'OK'):
                return True
        except Exception as e:
            logger.debug(f'Virtual display repin command unavailable: {e}')

        output = self.adb_shell(['dumpsys', 'activity', 'activities'])
        blocks = re.split(
            r'(?=^\s*(?:Display #\d+|Display:\s*mDisplayId=\d+))',
            output, flags=re.MULTILINE,
        )
        for block in blocks:
            display = re.search(r'(?:Display #|Display:\s*mDisplayId=)(\d+)', block)
            if not display or self.package not in block:
                continue
            display_id = int(display.group(1))
            if display_id == self._virtual_display_id:
                return True
            task = re.search(r'Task\{[^#]*#(\d+)', block)
            if not task:
                task = re.search(r'\btaskId[=:](\d+)', block)
            if not task:
                continue
            task_id = int(task.group(1))
            logger.warning(
                f'App task drifted to display {display_id}; move task {task_id} '
                f'to {self._virtual_display_id}'
            )
            result = self.adb_shell([
                'am', 'display', 'move-stack', task_id, self._virtual_display_id,
            ])
            if not re.search(r'(?i)error|exception|fail', result or ''):
                try:
                    self.app_start_adb()
                except Exception:
                    pass
                return True
            break
        try:
            self.app_start_adb()
            return True
        except Exception as e:
            logger.debug(f'Failed to relaunch app on virtual display: {e}')
            return False

    def _virtual_display_start(self):
        bridge = os.path.abspath(os.path.join('bin', 'virtual_display', 'nkas-vd-server.jar'))
        scrcpy_server = os.path.abspath(os.path.join('bin', 'scrcpy', 'scrcpy-server'))
        if not os.path.isfile(bridge) or not os.path.isfile(scrcpy_server):
            logger.critical('Virtual display bridge files are missing from ./bin/')
            raise RequestHumanTakeover

        remote_bridge = '/data/local/tmp/nkas-vd-server.jar'
        remote_scrcpy = '/data/local/tmp/nkas-scrcpy-server.jar'
        self.adb_push(bridge, remote_bridge)
        self.adb_push(scrcpy_server, remote_scrcpy)

        self._virtual_socket_name = f'nkas-vd-{os.getpid()}-{int(time.time() * 1000) % 1000000}'
        remote_command = (
            f'CLASSPATH={remote_bridge}:{remote_scrcpy} app_process / '
            'com.nkas.virtualdisplay.Server 720 1280 240 '
            f'{self._virtual_socket_name}'
        )
        command = [
            self.adb_binary, '-s', self.serial, 'shell', remote_command,
        ]
        logger.info(f'Start virtual display: {command}')
        self._virtual_display_ready.clear()
        try:
            self._virtual_display_process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', bufsize=1, shell=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError as e:
            logger.critical(f'Failed to start virtual display bridge: {e}')
            raise RequestHumanTakeover

        self._virtual_display_reader = threading.Thread(
            target=self._read_virtual_display_output, daemon=True
        )
        self._virtual_display_reader.start()
        if not self._virtual_display_ready.wait(timeout=15) or self._virtual_display_id is None:
            self._virtual_display_stop()
            logger.critical('Android did not create a virtual display within 15 seconds')
            raise RequestHumanTakeover

        self._virtual_capture_port = self.adb_forward(
            f'localabstract:{self._virtual_socket_name}'
        )
        self._refresh_virtual_display_info()
        self.app_stop_adb()
        self.app_start_adb()
        # Some vendor ROMs (notably One UI and several Android 16 builds) move
        # a newly launched task back to display 0. Give the bridge time to pin
        # it again, while keeping the capture thread free to deliver frames.
        started_at = time.time()
        deadline = started_at + 30
        ready_after = started_at + 10
        logger.info('Virtual display startup grace: 10s')
        # Let the game and SurfaceFlinger render their first frame before
        # starting the ADB capture polling loop on low-power physical devices.
        time.sleep(5)
        image = None
        attempts = 0
        stable_frames = 0
        last_info = 0.0
        last_repin = 0.0
        try:
            while time.time() < deadline:
                now = time.time()
                if now - last_info >= 1:
                    self._refresh_virtual_display_info()
                    last_info = now
                try:
                    image = self.screenshot_virtual_display()
                except ScreenshotSizeError as e:
                    # ImageReader has no buffer until the launched app renders its
                    # first frame; some Android 16 ROMs take several seconds.
                    if '/0' not in str(e):
                        raise
                    image = None
                # A task being reported on the target display is not proof that
                # SurfaceFlinger has produced a usable frame yet.  MatePad and
                # several Android 12/13 ROMs report the task first, while the
                # ImageReader still returns black buffers for a few seconds.
                # Keep polling until a non-black frame is available; otherwise
                # the old `app_on_target` shortcut stopped the loop and the
                # final black-frame check immediately tore the bridge down.
                frame_ready = image is not None and image.shape[:2] == (1280, 720) and image.max() > 20
                if frame_ready and now >= ready_after:
                    stable_frames += 1
                else:
                    stable_frames = 0
                if stable_frames >= 3:
                    break
                attempts += 1
                if now - last_repin >= 1.5:
                    self._repin_app_to_virtual_display()
                    last_repin = now
                # Keep a short focus nudge during the black-frame window even
                # when REPIN reports success.  ActivityOptions moves the task
                # to the display, but some OEM launchers do not start drawing
                # until the display receives an input event (the original
                # MatePad path relied on this behavior).
                if attempts <= 8 and attempts % 2 == 0:
                    try:
                        self._adb_input('tap', 360, 640)
                    except Exception:
                        pass
                time.sleep(0.5)
        except Exception as e:
            self._virtual_display_stop()
            logger.critical(f'Cannot capture the Android virtual display: {e}')
            raise RequestHumanTakeover
        if stable_frames < 3 or image is None or image.shape[:2] != (1280, 720) or image.max() <= 20:
            shape = None if image is None else image.shape
            self._virtual_display_stop()
            logger.critical(
                f'Virtual display did not provide 3 stable frames: '
                f'stable_frames={stable_frames}, shape={shape}'
            )
            raise RequestHumanTakeover

        logger.info(
            f'Virtual display ready: logical={self._virtual_display_id}, '
            f'capture=tcp:{self._virtual_capture_port}, '
            f'raw={self._virtual_display_raw_width}x{self._virtual_display_raw_height}, '
            f'rotation={self._virtual_display_rotation}'
        )
        atexit.register(self._virtual_display_stop)

    def _virtual_display_stop(self):
        process = self._virtual_display_process
        port = self._virtual_capture_port
        self._virtual_display_process = None
        if process is not None and process.poll() is None:
            logger.info('Stop virtual display')
            if port is not None:
                try:
                    self._virtual_display_command(b'STOP\n', timeout=2)
                except Exception:
                    pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        if port is not None:
            try:
                self.adb_forward_remove(f'tcp:{port}')
            except Exception:
                pass
        self._virtual_display_id = None
        self._virtual_capture_port = None
        self._virtual_socket_name = None
        self._virtual_display_width = 720
        self._virtual_display_height = 1280
        self._virtual_display_rotation = 0
        self._virtual_display_raw_width = 720
        self._virtual_display_raw_height = 1280

    @staticmethod
    def _wm_override_value(output: str, key: str):
        """wm size/density 输出中存在 Override 行时返回其值，否则 None。"""
        match = re.search(rf'Override {key}: (\S+)', output)
        return match.group(1) if match else None

    def _physical_device_debug_state(self, stage: str):
        """分辨率/方向改动前后的设备显示状态快照，仅 debug 级输出，用于排查厂商 ROM 差异。"""
        try:
            lines = [
                self.adb_shell(['wm', 'size']).strip(),
                self.adb_shell(['wm', 'density']).strip(),
                f"accelerometer_rotation={self.adb_shell(['settings', 'get', 'system', 'accelerometer_rotation']).strip()}",
                f"user_rotation={self.adb_shell(['settings', 'get', 'system', 'user_rotation']).strip()}",
            ]
            # DisplayDeviceInfo 行同时含物理(init=)与当前(cur=)显示参数及实际旋转角
            for line in self.adb_shell(['dumpsys', 'window', 'displays']).splitlines():
                if 'init=' in line:
                    lines.append(line.strip())
                    break
            logger.debug(f'Physical device state ({stage}): ' + ' | '.join(lines))
        except Exception as e:
            logger.debug(f'Physical device state ({stage}) snapshot failed: {e}')

    def _physical_device_resolution_set(self):
        """
        真机模式：临时把设备分辨率改为模板基准 720x1280。
        AutoRestoreResolution 开启时进程正常退出自动恢复，被强杀时由 webui 侧 ProcessManager.stop 兜底
        （兜底路径拿不到 override 记录，仍是 reset，属异常路径可接受）；关闭时由用户在设置页手动还原。
        只改 wm size 不改 density 会导致 UI 按原 dp 尺寸渲染，画面等比放大（图标出屏），
        density 固定为 240，与模拟器的 720x1280@240dpi 一致。
        """
        logger.info(
            'Physical device: '
            f'{self.adb_getprop("ro.product.model")} '
            f'(Android {self.adb_getprop("ro.build.version.release")}, '
            f'SDK {self.adb_getprop("ro.build.version.sdk")})'
        )
        self._physical_device_debug_state('before set')
        size_out = self.adb_shell(['wm', 'size'])
        logger.info(f'Physical device display: {size_out}')
        density_out = self.adb_shell(['wm', 'density'])
        logger.info(f'Physical device density: {density_out}')
        # 记录用户已有的 override，退出时还原具体值；直接 reset 会丢掉用户自己的分辨率/DPI 覆盖
        self._physical_size_override = self._wm_override_value(size_out, 'size')
        self._physical_density_override = self._wm_override_value(density_out, 'density')
        self.adb_shell(['wm', 'size', '720x1280'])
        self.adb_shell(['wm', 'density', '240'])
        # 锁定竖屏：关闭自动旋转并固定 user_rotation=0（自然方向，手机即竖屏），
        # 否则设备平放/旋转时画面会横过来，截图与坐标全部错位
        self._physical_rotation_backup = {
            key: self.adb_shell(['settings', 'get', 'system', key]).strip()
            for key in ('accelerometer_rotation', 'user_rotation')
        }
        self.adb_shell(['settings', 'put', 'system', 'accelerometer_rotation', '0'])
        self.adb_shell(['settings', 'put', 'system', 'user_rotation', '0'])
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
        self._physical_device_debug_state('after set')
        if self.config.PhysicalDevice_AutoRestoreResolution:
            atexit.register(self._physical_device_resolution_reset)

    def _physical_device_resolution_reset(self):
        logger.info('Restore physical device resolution')
        self._physical_device_debug_state('before reset')
        size = getattr(self, '_physical_size_override', None) or 'reset'
        density = getattr(self, '_physical_density_override', None) or 'reset'
        rotation = getattr(self, '_physical_rotation_backup', None) or {}
        try:
            self.adb_shell(['wm', 'size', size])
            self.adb_shell(['wm', 'density', density])
            for key, value in rotation.items():
                # settings get 在未设置过时返回 null，跳过以免写入字面量
                if value and value != 'null':
                    self.adb_shell(['settings', 'put', 'system', key, value])
            self._physical_device_debug_state('after reset')
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
