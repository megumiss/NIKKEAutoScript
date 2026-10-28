import re
import time

from module.config.account import UNLOCK_PASSWORD_FIELD, load_secret
from module.device.adb.connection import Connection
from module.exception import RequestHumanTakeover
from module.logger import logger

# 各厂商 ROM 的 dumpsys 字段不统一，兼容新机型时在这里补充即可
KEYGUARD_STATE_COMMAND = (
    'dumpsys window policy; '
    'dumpsys activity activities | grep -i keyguard; '
    'dumpsys window | grep mCurrentFocus'
)
KEYGUARD_SHOWING_PATTERNS = [
    re.compile(r'\bm?KeyguardShowing=true', re.IGNORECASE),
    re.compile(r'\bmShowingLockscreen=true'),
    re.compile(r'\bmDreamingLockscreen=true'),
    re.compile(r'\bisStatusBarKeyguard=true'),
]
KEYGUARD_HIDDEN_PATTERNS = [
    re.compile(r'\bm?KeyguardShowing=false', re.IGNORECASE),
    re.compile(r'\bmShowingLockscreen=false'),
    re.compile(r'\bisStatusBarKeyguard=false'),
]
# 仅在以上字段都缺失时使用：未锁屏下拉通知栏时焦点同样是 NotificationShade，不能优先于明确字段。
# 锁屏时焦点在系统锁屏窗口，Android 12+ 为 NotificationShade，更早为 StatusBar
FOCUS_PATTERN = re.compile(r'mCurrentFocus=Window\{[^}]*\}')
FOCUS_KEYGUARD_PATTERN = re.compile(r'\b(?:NotificationShade|StatusBar|Keyguard)\b')
UNLOCK_CONFIRM_TIMEOUT = 5
DIGIT_KEYCODES = {str(i): f'KEYCODE_{i}' for i in range(10)}


class ScreenUnlock(Connection):
    """
    真机锁屏时通过 ADB 虚拟按键唤醒并解锁。

    锁屏界面只存在于主屏，所以按键一律直接走 `adb shell input`，
    不能用 adb_shell_input_keyevent / adb_shell_input_text：开启虚拟屏后它们会把输入转发到虚拟屏。
    """

    @staticmethod
    def parse_keyguard_showing(output: str):
        """
        Returns:
            bool | None: True 锁屏，False 未锁屏，None 输出中没有任何已知字段
        """
        if any(pattern.search(output) for pattern in KEYGUARD_SHOWING_PATTERNS):
            return True
        if any(pattern.search(output) for pattern in KEYGUARD_HIDDEN_PATTERNS):
            return False
        focus = FOCUS_PATTERN.search(output)
        if focus:
            return bool(FOCUS_KEYGUARD_PATTERN.search(focus.group(0)))
        return None

    def _screen_is_locked(self) -> bool:
        state = self.parse_keyguard_showing(self.adb_shell(KEYGUARD_STATE_COMMAND))
        if state is None:
            # 不能按"锁屏"继续输密码：误判时密码会被输进前台应用
            logger.warning('Cannot detect lock screen state on this device, treat as unlocked. '
                           f'Please report the output of `adb shell "{KEYGUARD_STATE_COMMAND}"`')
            return False
        return state

    def _wait_unlocked(self, timeout=UNLOCK_CONFIRM_TIMEOUT) -> bool:
        """低端机解锁动画较慢，轮询确认而不是固定等待"""
        deadline = time.time() + timeout
        while 1:
            time.sleep(0.5)
            if not self._screen_is_locked():
                return True
            if time.time() >= deadline:
                return False

    def _unlock_keyevent(self, *keycodes):
        self.adb_shell(['input', 'keyevent', *keycodes])

    @staticmethod
    def parse_display_size(output: str):
        """解析 wm size 输出，存在 Override 时以它为准（真机模式会改成 720x1280）"""
        size = None
        for key in ('Physical', 'Override'):
            match = re.search(rf'{key} size: (\d+)x(\d+)', output)
            if match:
                size = int(match.group(1)), int(match.group(2))
        return size

    def _show_bouncer(self):
        """
        上滑调出密码输入界面，无安全锁时直接解锁。
        不用 wm dismiss-keyguard：部分 ROM（如开启人脸解锁的华为）会先进入人脸识别而不显示密码界面。
        """
        size = self.parse_display_size(self.adb_shell(['wm', 'size'])) or (720, 1280)
        width, height = size
        x = width // 2
        self.adb_shell(['input', 'swipe', x, int(height * 0.9), x, int(height * 0.3), 300])

    def _input_password(self, password: str):
        if password.isdigit() and password.isascii():
            self._unlock_keyevent(*(DIGIT_KEYCODES[c] for c in password))
        else:
            self.adb_shell(f'input text {self.adb_shell_text_escape(password)}')
        self._unlock_keyevent('KEYCODE_ENTER')
        logger.info(f'Screen unlock: entered {len(password)}-character password')

    def screen_unlock(self):
        """
        Raises:
            RequestHumanTakeover: 输入密码后仍处于锁屏
        """
        if not self.config.PhysicalDevice_Enable or not self.config.PhysicalDevice_AutoUnlock:
            return
        logger.hr('Screen unlock', level=2)

        # WAKEUP 对已亮屏无副作用，无条件发送，不依赖各 ROM 不一致的亮屏字段（如三星 AOD）
        self._unlock_keyevent('KEYCODE_WAKEUP')
        time.sleep(1)
        if not self._screen_is_locked():
            logger.info('Screen is not locked')
            return

        logger.info('Screen is locked, swipe up to show bouncer')
        self._show_bouncer()
        time.sleep(1)
        # 上滑期间人脸识别或无密码锁屏可能已经解锁
        if not self._screen_is_locked():
            logger.info('Screen unlocked')
            return
        # 配置值为空表示用户已清空密码，此时 .acc 中可能残留旧密码，不能读取
        if self.config.PhysicalDevice_Password:
            password = load_secret(self.config.config_name, UNLOCK_PASSWORD_FIELD)
            if password:
                self._input_password(password)
            else:
                logger.warning('Screen unlock password is set but cannot be decrypted, please re-enter it')

        # 连续输错会触发系统锁定，失败后只交给用户处理，不重试
        if not self._wait_unlocked():
            logger.critical('Failed to unlock screen, please check the lock screen password '
                            'or unlock the device manually')
            raise RequestHumanTakeover
        logger.info('Screen unlocked')
