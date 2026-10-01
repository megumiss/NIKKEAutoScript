"""Bounded standalone control and failure reporting, independent of task scheduling."""
import functools
import json
import signal
import subprocess
import sys
import time

import cv2

from dev_tools.minimap_reconstruct import DriverWindow
from . import settings

_active = None


def write_image(path, image, *args):
    """保存证据图片并检查 OpenCV 返回值，写盘失败必须上报而不能继续假装已留证。"""
    if image is None or not cv2.imwrite(str(path), image, *args):
        raise OSError(f'Could not save evidence image: {path}')
    return True


def check_stop():
    """检查共享停止文件，以 KeyboardInterrupt 进入统一取消和释放路径。"""
    if settings.stop_file.exists():
        raise KeyboardInterrupt(f'Stop requested: {settings.stop_file}')


def pause(seconds):
    """用短间隔等待保持停止响应，并在持有窗口时持续检查焦点和坐标有效性。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_stop()
        if _active is not None:
            _active.check()
        time.sleep(min(.1, max(0, deadline - time.monotonic())))


def command(main):
    """为独立命令统一错误输出；取消退出 130，运行失败退出 1。"""
    @functools.wraps(main)
    def run(*args, **kwargs):
        """执行被包装的入口，向标准错误输出结构化失败原因并保留异常链。"""
        try:
            return main(*args, **kwargs)
        except KeyboardInterrupt as exc:
            print(json.dumps({'status': 'cancelled', 'reason': str(exc)}), file=sys.stderr, flush=True)
            raise SystemExit(130) from exc
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, cv2.error, subprocess.SubprocessError) as exc:
            print(json.dumps({'status': 'failed', 'reason': str(exc)}), file=sys.stderr, flush=True)
            raise SystemExit(1) from exc
    return run


class GuardedInput:
    def __init__(self, window, handler):
        """将虚拟鼠标绑定到当前受保护窗口，所有新输入都必须经过状态检查。"""
        self.window, self.handler = window, handler

    def __getattr__(self, name):
        """代理驱动属性，仅包装会产生新输入的方法；mouse_up 保持可用以便异常清理。"""
        value = getattr(self.handler, name)
        if name not in ('mouse_click', 'mouse_swipe', 'mouse_down', 'mouse_move'):
            return value

        def send(*args, **kwargs):
            """在发送手势前检查停止、焦点及窗口几何，发送后立即检查驱动结果。"""
            self.window.check()
            if self.window.gui.GetForegroundWindow() != self.window.hwnd:
                origin = self.window.gui.ClientToScreen(self.window.hwnd, (0, 0))
                title = (origin[0] + 400, origin[1] - 20)
                activating = (name == 'mouse_click' and args == title
                              and getattr(self.window, '_focus_activation', False)
                              and not self.window._focused
                              and self.window.gui.GetAncestor(self.window.gui.WindowFromPoint(title), 2)
                              == self.window.hwnd)
                if not activating:
                    raise RuntimeError('Game is not foreground; no input sent')
            result = value(*args, **kwargs)
            if self.handler._failures:
                raise RuntimeError(f'Driver gesture failed: {name}')
            return result
        return send


class Window(DriverWindow):
    def __init__(self, args=None):
        """建立单个活动控制窗口并安装中断处理，后续初始化失败也必须释放已取得的驱动。"""
        global _active
        check_stop()
        if _active is not None:
            raise RuntimeError('Another prototype window is already active')
        self._focused = False
        self._closed = False
        self._signals = {}
        super().__init__(args or settings.driver_args())
        try:
            self._origin = self.gui.ClientToScreen(self.hwnd, (0, 0))
            self.handler = GuardedInput(self, self.handler)
            _active = self
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._signals[sig] = signal.getsignal(sig)
                signal.signal(sig, self._interrupt)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _interrupt(signum, frame):
        """把进程中断转换成 Python 取消异常，使入口的 finally 有机会释放鼠标。"""
        raise KeyboardInterrupt(f'Signal {signum}')

    def check(self):
        """复用采集器窗口校验并检查原型共享停止信号，禁止窗口变化后继续使用旧屏幕坐标。"""
        check_stop()
        super().check()
        if hasattr(self, '_origin') and self.gui.ClientToScreen(self.hwnd, (0, 0)) != self._origin:
            raise RuntimeError('Game window moved; screen coordinates invalidated')
        if self._focused and self.gui.GetForegroundWindow() != self.hwnd:
            raise RuntimeError('Game focus lost; no further input')

    def focus(self):
        """首次取得焦点后锁定前台条件，后续调用不能掩盖用户切换窗口。"""
        self.check()
        # 首次从浏览器启动时可能需要标题栏激活；只有已核实的标题栏坐标可绕过前台条件。
        self._focus_activation = True
        try:
            super().focus()
        finally:
            self._focus_activation = False
        self._focused = True

    def close(self):
        """幂等释放窗口与鼠标，清除活动实例并恢复原有信号处理器。"""
        global _active
        if self._closed:
            return
        self._closed = True
        try:
            super().close()
        finally:
            _active = None
            for sig, handler in self._signals.items():
                signal.signal(sig, handler)


def finish(window, receipt, report):
    """先释放输入再写回执；写盘失败时输出备用诊断，保留已有取消或操作异常。

    Release before writing evidence, including when the original operation failed.
    """
    error = sys.exc_info()[1]
    result = dict(report) if isinstance(report, dict) else {'steps': report}
    result['status'] = 'cancelled' if isinstance(error, KeyboardInterrupt) else 'failed' if error else 'completed'
    if error is not None:
        result['reason'] = str(error)
    release_error = None
    try:
        window.close()
    except BaseException as exc:
        release_error = exc
        result.update(status='failed', release_error=str(exc))
    try:
        receipt.write_text(json.dumps(result, indent=2), encoding='utf-8')
    except OSError as exc:
        result['receipt_error'] = str(exc)
        if error is None:
            result['status'] = 'failed'
        print(json.dumps(result), file=sys.stderr, flush=True)
        if error is None and release_error is None:
            raise
    if release_error is not None:
        raise release_error


def run_child(cmd, log, timeout=180):
    """串行等待子进程并传播非零退出码；超时或取消通过共享 STOP 请求子进程释放后退出。

    A stop request is forwarded to the child through the shared stop file.
    """
    check_stop()
    with log.open('w', encoding='utf-8') as stream:
        child = subprocess.Popen(cmd, stdout=stream, stderr=subprocess.STDOUT, cwd=settings.ROOT)
        try:
            deadline = time.monotonic() + timeout
            while child.poll() is None:
                check_stop()
                if time.monotonic() >= deadline:
                    raise TimeoutError('Camera child timed out')
                time.sleep(.1)
        except BaseException:
            try:
                settings.stop_file.parent.mkdir(parents=True, exist_ok=True)
                settings.stop_file.touch()
            except OSError as exc:
                print(f'Could not signal child through STOP: {exc}', file=sys.stderr, flush=True)
            finally:
                # 留出时间让子进程进入 finally；停止文件写盘失败也必须回收进程。
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            raise
        if child.returncode:
            raise RuntimeError(f'Camera child failed ({child.returncode}); see {log}')
