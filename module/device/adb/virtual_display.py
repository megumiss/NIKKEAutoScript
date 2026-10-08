import os
import re
import subprocess
import threading
import time
from types import SimpleNamespace

from module.device.adb.screenshot import Screenshot, ScreenshotSizeError
from module.exception import RequestHumanTakeover
from module.logger import logger


class VirtualDisplayBridge(Screenshot):
    def __init__(self, record, persist, cancelled=lambda: False):
        self.record = record
        self.persist = persist
        self.cancelled = cancelled
        self.config = SimpleNamespace(PhysicalDevice_VirtualDisplayId=record['identity'])
        self.serial = record['serial']
        self.package = record['package']
        self.adb_binary = record['adb_binary']
        self._virtual_display_process = None
        self._virtual_display_reader = None
        self._virtual_display_ready = threading.Event()
        self._virtual_display_id = record.get('display_id')
        self._virtual_capture_port = record.get('port')
        self._virtual_socket_name = record['socket']
        self._virtual_display_width = 720
        self._virtual_display_height = 1280
        self._virtual_display_raw_width = 720
        self._virtual_display_raw_height = 1280
        self._virtual_display_rotation = 0

    def check_cancelled(self):
        if self.cancelled():
            raise RequestHumanTakeover('Virtual display operation was cancelled')

    def command(self, args, timeout=10):
        try:
            result = subprocess.run(
                [self.adb_binary, '-s', self.serial, *map(str, args)],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f'ADB did not respond within {timeout}s') from exc
        if result.returncode:
            raise OSError(result.stderr.strip() or result.stdout.strip())
        return result.stdout.strip()

    def adb_shell(self, command, timeout=10):
        import shlex
        self.check_cancelled()
        if not isinstance(command, str):
            command = shlex.join(map(str, command))
        return self.command(['shell', command], timeout=timeout)

    def adb_push(self, source, destination):
        self.check_cancelled()
        return self.command(['push', source, destination], timeout=30)

    def adb_forward(self, remote):
        self.check_cancelled()
        port = int(self.command(['forward', 'tcp:0', remote]))
        self.record['port'] = port
        self.persist(self.record)
        return port

    def adb_forward_remove(self, local):
        return self.command(['forward', '--remove', local])

    def device_identity(self):
        uid = self.command(['shell', 'getprop ro.serialno'])
        boot = self.command(['shell', 'cat /proc/sys/kernel/random/boot_id'])
        if not uid or not re.fullmatch(r'[a-f0-9-]{36}', boot):
            raise RuntimeError('Cannot verify physical device identity')
        return uid, boot

    def remove_forward(self):
        port = self.record.get('port')
        if port is None:
            return
        target = f'localabstract:{self._virtual_socket_name}'
        for line in self.command(['forward', '--list']).splitlines():
            if line.split() == [self.serial, f'tcp:{port}', target]:
                self.adb_forward_remove(f'tcp:{port}')
        self._virtual_capture_port = None
        self.record['port'] = None
        self.persist(self.record)

    def probe(self):
        uid, boot = self.device_identity()
        if uid != self.record['device_uid'] or boot != self.record['boot_id']:
            raise RuntimeError('Device or boot changed')
        expected = [self.serial, f'tcp:{self._virtual_capture_port}', f'localabstract:{self._virtual_socket_name}']
        forwards = [line.split() for line in self.command(['forward', '--list']).splitlines()]
        if expected not in forwards:
            self._virtual_capture_port = self.adb_forward(f'localabstract:{self._virtual_socket_name}')
        try:
            response = self._virtual_display_command(b'INFO\n', timeout=3).decode('utf-8', errors='replace')
        except ScreenshotSizeError as exc:
            raise RuntimeError('Virtual display bridge did not return valid INFO') from exc
        fields = dict(re.findall(r'(\w+)=([^\s]+)', response))
        if fields.get('identity') != self.record['identity'] or fields.get('socket') != self._virtual_socket_name:
            raise RuntimeError('Virtual display bridge identity does not match')
        info = self._bridge_display_info(response)
        if not info or info['id'] <= 0 or not info['width'] or not info['height'] \
                or not fields.get('pid', '').isdigit():
            raise RuntimeError('Virtual display is unavailable')
        self._virtual_display_id = info['id']
        self._virtual_display_raw_width = info['width']
        self._virtual_display_raw_height = info['height']
        self._virtual_display_rotation = info['rotation']
        changed = self.record.get('display_id') != info['id'] or self.record.get('server_pid') != int(fields['pid'])
        self.record.update(display_id=info['id'], server_pid=int(fields['pid']))
        if changed:
            self.persist(self.record)
        return dict(self.record, width=info['width'], height=info['height'], rotation=info['rotation'],
                    generation=self._virtual_socket_name)

    def server_pids(self):
        output = self.command(['shell', 'ps -A -o PID,ARGS'])
        result = []
        for line in output.splitlines():
            fields = line.split()
            if fields and fields[0].isdigit() and self._virtual_socket_name in fields \
                    and 'com.nkas.virtualdisplay.Server' in fields:
                result.append(int(fields[0]))
        return result

    def close(self):
        cancelled, self.cancelled = self.cancelled, lambda: False
        try:
            uid, boot = self.device_identity()
            if uid != self.record['device_uid']:
                raise RuntimeError('Refusing to release resources on a different device')
            if boot == self.record['boot_id']:
                pids = self.server_pids()
                if pids:
                    # A unique socket token in the process arguments proves ownership even before READY.
                    for pid in pids:
                        self.command(['shell', f'kill -TERM {pid}'])
                    deadline = time.monotonic() + 5
                    while self.server_pids() and time.monotonic() < deadline:
                        time.sleep(0.1)
                    if self.server_pids():
                        raise RuntimeError('Virtual display server did not exit')
                if f'NIKKE-{self.record["identity"]}' in self.command(['shell', 'dumpsys display']):
                    raise RuntimeError('Display still exists; ownership or release could not be confirmed')
            self.remove_forward()
            process = self._virtual_display_process
            if process is not None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=3)
            self._virtual_display_id = None
        finally:
            self.cancelled = cancelled

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
                    fields = dict(re.findall(r'(\w+)=([^\s]+)', line))
                    if fields.get('pid', '').isdigit():
                        self.record['server_pid'] = int(fields['pid'])
                        self.record['display_id'] = display_id
                        self.persist(self.record)
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

        virtual_display_id = str(self.config.PhysicalDevice_VirtualDisplayId or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9]{12}', virtual_display_id):
            raise RequestHumanTakeover('Invalid virtual display identity')
        remote_command = (
            f'CLASSPATH={remote_bridge}:{remote_scrcpy} app_process / '
            'com.nkas.virtualdisplay.Server 720 1280 240 '
            f'{self._virtual_socket_name}'
            + (f' nkas-id-{virtual_display_id}' if virtual_display_id else '')
        )
        command = [
            self.adb_binary, '-s', self.serial, 'shell', remote_command,
        ]
        logger.info(f'Start virtual display: {command}')
        self.check_cancelled()
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
            logger.critical('Android did not create a virtual display within 15 seconds')
            raise RequestHumanTakeover

        self._virtual_capture_port = self.adb_forward(
            f'localabstract:{self._virtual_socket_name}'
        )
        self.probe()
        self.check_cancelled()
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
        for _ in range(10):
            self.check_cancelled()
            time.sleep(0.5)
        image = None
        attempts = 0
        stable_frames = 0
        last_info = 0.0
        last_repin = 0.0
        try:
            while time.time() < deadline:
                self.check_cancelled()
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
            logger.critical(f'Cannot capture the Android virtual display: {e}')
            raise RequestHumanTakeover
        if stable_frames < 3 or image is None or image.shape[:2] != (1280, 720) or image.max() <= 20:
            shape = None if image is None else image.shape
            logger.critical(
                f'Virtual display did not provide 3 stable frames: '
                f'stable_frames={stable_frames}, shape={shape}'
            )
            raise RequestHumanTakeover

        logger.info(
            f'Virtual display ready: identity={virtual_display_id}, '
            f'logical={self._virtual_display_id}, '
            f'capture=tcp:{self._virtual_capture_port}, '
            f'raw={self._virtual_display_raw_width}x{self._virtual_display_raw_height}, '
            f'rotation={self._virtual_display_rotation}'
        )

