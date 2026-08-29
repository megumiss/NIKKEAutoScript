import re
import time
from functools import wraps

import cv2
import numpy as np
from adbutils.errors import AdbError

from module.device.adb.connection import Connection
from module.device.adb.method.utils import (
    ImageTruncated, PackageNotInstalled, RETRY_TRIES, handle_adb_error, handle_unknown_host_service, retry_sleep)
from module.exception import RequestHumanTakeover
from module.logger import logger


def retry(func):
    @wraps(func)
    def retry_wrapper(self, *args, **kwargs):
        """
        Args:
            self (Adb):
        """
        init = None
        for _ in range(RETRY_TRIES):
            try:
                if callable(init):
                    time.sleep(retry_sleep(_))
                    init()
                return func(self, *args, **kwargs)
            # Can't handle
            except RequestHumanTakeover:
                break
            # When adb server was killed
            except ConnectionResetError as e:
                logger.error(e)

                def init():
                    self.adb_reconnect()
            # AdbError
            except AdbError as e:
                if handle_adb_error(e):
                    def init():
                        self.adb_reconnect()
                elif handle_unknown_host_service(e):
                    def init():
                        self.adb_start_server()
                        self.adb_reconnect()
                else:
                    break
            # Package not installed
            except PackageNotInstalled as e:
                logger.error(e)

                def init():
                    self.detect_package()
            # Unknown
            except Exception as e:
                logger.exception(e)

                def init():
                    pass

        logger.critical(f'Retry {func.__name__}() failed')
        raise RequestHumanTakeover

    return retry_wrapper


class Adb(Connection):
    @property
    def effective_control_method(self):
        if getattr(self, '_virtual_display_id', None) is not None:
            return 'ADB'
        return self.config.Emulator_ControlMethod

    def _adb_input(self, *args):
        args = list(args)
        display_id = getattr(self, '_virtual_display_id', None)
        if display_id is not None:
            transform = getattr(self, '_virtual_display_transform_point', None)
            if callable(transform) and args:
                try:
                    if args[0] == 'tap' and len(args) >= 3:
                        args[1], args[2] = transform(args[1], args[2])
                    elif args[0] == 'swipe' and len(args) >= 5:
                        args[1], args[2] = transform(args[1], args[2])
                        args[3], args[4] = transform(args[3], args[4])
                except (TypeError, ValueError):
                    logger.debug(f'Unable to transform virtual-display input: {args!r}')
            command = ['input', '-d', display_id]
        else:
            command = ['input']
        command.extend(args)
        return self.adb_shell(command)

    @retry
    def screenshot_adb(self):
        """
        adb exec-out screencap。PNG 全质量输出，代价是每次约 1s 量级（视连接方式而定）。
        """
        data = self.adb_command(['exec-out', 'screencap', '-p'], timeout=10)
        image = np.frombuffer(data, np.uint8)
        image = cv2.imdecode(image, cv2.IMREAD_COLOR)
        if image is None:
            raise ImageTruncated('Empty image from adb screencap')

        cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=image)

        return image

    @retry
    def app_current_adb(self):
        """
        Returns:
            str: Package name of the current focused app.
        """
        display_id = getattr(self, '_virtual_display_id', None)
        if display_id is not None:
            output = self.adb_shell(['dumpsys', 'activity', 'activities'])
            blocks = re.split(
                r'(?=^\s*(?:Display #\d+|Display:\s*mDisplayId=\d+))',
                output, flags=re.MULTILINE,
            )
            for block in blocks:
                header = re.search(
                    r'^\s*(?:Display #|Display:\s*mDisplayId=)(\d+)',
                    block, re.MULTILINE,
                )
                if not header or int(header.group(1)) != display_id:
                    continue
                activity = re.search(
                    r'ActivityRecord\{[^}]*?\s(?P<package>[\w.]+)/[^\s}]+',
                    block,
                )
                if activity:
                    return activity.group('package')
                component = re.search(
                    r'(?:topActivity|baseActivity|realActivity|mResumedActivity)\s*[=:]\s*'
                    r'(?:ComponentInfo\{)?(?P<package>[\w.]+)/[^\s}\]]+',
                    block,
                )
                if component:
                    return component.group('package')
            raise OSError(f"Couldn't get focused app on display {display_id}")

        _focusedRE = re.compile(
            r'mCurrentFocus=Window{.*\s+(?P<package>[^\s]+)/(?P<activity>[^\s]+)\}'
        )
        m = _focusedRE.search(self.adb_shell(['dumpsys', 'window', 'windows']))
        if m:
            return m.group('package')

        _activityRE = re.compile(
            r'ACTIVITY (?P<package>[^\s]+)/(?P<activity>[^/\s]+) \w+ pid=(?P<pid>\d+)'
        )
        output = self.adb_shell(['dumpsys', 'activity', 'top'])
        ret = None
        for m in _activityRE.finditer(output):
            ret = m.group('package')
        if ret:
            return ret
        raise OSError("Couldn't get focused app")

    @retry
    def app_start_adb(self, package_name=None):
        """
        通过 monkey 启动应用，无需知道 activity 名。
        """
        if not package_name:
            package_name = self.package
        display_id = getattr(self, '_virtual_display_id', None)
        if display_id is not None:
            try:
                response = self._virtual_display_command(
                    f'START {package_name}\n'.encode('utf-8'), timeout=5
                )
                if isinstance(response, bytes) and response.startswith(b'OK'):
                    return
                logger.debug(f'Virtual display START fallback: {response!r}')
            except Exception as e:
                logger.debug(f'Virtual display START command unavailable: {e}')
            resolved = self.adb_shell([
                'cmd', 'package', 'resolve-activity', '--brief',
                '-a', 'android.intent.action.MAIN',
                '-c', 'android.intent.category.LAUNCHER', package_name,
            ])
            component = next((line.strip() for line in reversed(resolved.splitlines()) if '/' in line), '')
            if not component:
                raise PackageNotInstalled(package_name)
            result = self.adb_shell(['am', 'start', '--display', display_id, '-n', component])
            if 'Error:' in result or 'Exception' in result:
                raise OSError(result)
            return
        result = self.adb_shell([
            'monkey', '-p', package_name, '-c',
            'android.intent.category.LAUNCHER', '--pct-syskeys', '0', '1'
        ])
        if 'No activities found' in result:
            logger.error(result)
            raise PackageNotInstalled(package_name)

    @retry
    def app_stop_adb(self, package_name=None):
        if not package_name:
            package_name = self.package
        self.adb_shell(['am', 'force-stop', package_name])

    @retry
    def click_adb(self, x, y):
        start = time.time()
        self._adb_input('tap', x, y)
        if time.time() - start <= 0.05:
            self.sleep(0.05)

    @retry
    def swipe_adb(self, p1, p2, duration=0.1):
        duration = int(duration * 1000)
        self._adb_input('swipe', *p1, *p2, duration)
