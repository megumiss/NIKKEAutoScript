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
    """保存证据图片并检查 OpenCV 返回值，写盘失败必须上报而不能继续假装已留证。

    path 为输出路径，image 为 OpenCV 图像，附加参数直接传给 imwrite。
    写入返回失败时抛出 OSError，避免生成成功回执却缺失现场证据。
    """
    if image is None or not cv2.imwrite(str(path), image, *args):
        raise OSError(f'Could not save evidence image: {path}')
    return True


def check_stop():
    """检查共享停止文件，以 KeyboardInterrupt 进入统一取消和释放路径。

    检查 settings.stop_file 是否存在。
    存在即抛出 KeyboardInterrupt，停止文件由操作者移除；此检查本身不访问窗口或清理文件。
    """
    if settings.stop_file.exists():
        raise KeyboardInterrupt(f'Stop requested: {settings.stop_file}')


def pause(seconds):
    """用短间隔等待保持停止响应，并在持有窗口时持续检查焦点和坐标有效性。

    使用单调时钟等待指定秒数，每隔最多 0.1 秒检查停止信号。
    有活动窗口时同时检查焦点与几何；取消及窗口异常直接传播，使等待阶段也能及时退出。
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_stop()
        if _active is not None:
            _active.check()
        time.sleep(min(.1, max(0, deadline - time.monotonic())))


def command(main):
    """为独立命令统一错误输出；取消退出 130，运行失败退出 1。

    包装命令入口并保留原函数元数据及成功返回值。
    已知运行错误以结构化 JSON 写到 stderr，取消和失败分别转换为 130、1；资源释放仍由入口 finally 完成。
    """
    @functools.wraps(main)
    def run(*args, **kwargs):
        """执行被包装的入口，向标准错误输出结构化失败原因并保留异常链。

        执行被装饰入口并区分用户取消与预期的文件、参数、图像和子进程错误。
        错误 JSON 包含 status 和 reason，SystemExit 保留异常链，不把失败变成正常命令返回。
        """
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
        """将虚拟鼠标绑定到当前受保护窗口，所有新输入都必须经过状态检查。

        window 提供停止、焦点与几何校验，handler 是实际输入驱动。
        这里只保存代理关系，不发送输入；产生输入的方法由 __getattr__ 包装后执行。
        """
        self.window, self.handler = window, handler

    def __getattr__(self, name):
        """代理驱动属性，仅包装会产生新输入的方法；mouse_up 保持可用以便异常清理。

        普通属性和释放鼠标操作直接委托给底层驱动；产生新输入的方法返回检查包装器。
        保留 mouse_up 的直接释放能力，确保失焦或取消后仍能结束已按下的鼠标状态。
        """
        value = getattr(self.handler, name)
        if name not in ('mouse_click', 'mouse_swipe', 'mouse_down', 'mouse_move'):
            return value

        def send(*args, **kwargs):
            """在发送手势前检查停止、焦点及窗口几何，发送后立即检查驱动结果。

            发送前检查停止文件、窗口位置和前台，唯一例外是经过窗口归属验证的首次标题栏激活。
            驱动执行后检查失败计数并原样返回结果；失焦或手势失败直接抛错，不继续下一次输入。
            """
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
        """建立单个活动控制窗口并安装中断处理，后续初始化失败也必须释放已取得的驱动。

        args 默认使用固定客户区及 ROI 设置，初始化时校验停止信号和单活动实例约束。
        记录客户区原点并注册中断处理；底层驱动创建后的后续初始化失败会进入 close 释放。
        """
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
        """把进程中断转换成 Python 取消异常，使入口的 finally 有机会释放鼠标。

        信号处理器把 SIGINT/SIGTERM 转换为 KeyboardInterrupt。
        不在处理器中直接关闭驱动，让调用栈的 finally 按既定顺序释放鼠标并写回执。
        """
        raise KeyboardInterrupt(f'Signal {signum}')

    def check(self):
        """复用采集器窗口校验并检查原型共享停止信号，禁止窗口变化后继续使用旧屏幕坐标。

        继承采集器的窗口校验，并比较初始化时的客户区屏幕原点。
        窗口移动或首次聚焦后失去前台会抛错，防止继续使用已失效的屏幕坐标。
        """
        check_stop()
        super().check()
        if hasattr(self, '_origin') and self.gui.ClientToScreen(self.hwnd, (0, 0)) != self._origin:
            raise RuntimeError('Game window moved; screen coordinates invalidated')
        if self._focused and self.gui.GetForegroundWindow() != self.hwnd:
            raise RuntimeError('Game focus lost; no further input')

    def focus(self):
        """首次取得焦点后锁定前台条件，后续调用不能掩盖用户切换窗口。

        首次聚焦允许受约束的标题栏激活，成功后设置前台锁定标记。
        再次调用会先执行 check，因此用户切换窗口不会被自动抢回焦点而掩盖。
        """
        self.check()
        # 首次从浏览器启动时可能需要标题栏激活；只有已核实的标题栏坐标可绕过前台条件。
        self._focus_activation = True
        try:
            super().focus()
        finally:
            self._focus_activation = False
        self._focused = True

    def close(self):
        """幂等释放窗口与鼠标，清除活动实例并恢复原有信号处理器。

        重复调用立即返回；首次调用先标记关闭，再释放底层窗口。
        即使底层释放异常，也会清除活动实例并恢复信号处理器，避免后续命令继承过期状态。
        """
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

    report 可以是状态字典或步骤列表，receipt 为最终 JSON 路径；状态取决于正在传播的异常。
    先尝试关闭窗口再写盘，写盘失败向 stderr 输出备用证据；释放失败会显式传播，不宣称清理成功。
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

    把子进程 stdout/stderr 合并到 log，在仓库目录运行并轮询共享停止信号及超时。
    异常时创建 STOP，给子进程 15 秒释放资源，超时再回收；正常结束仍检查非零退出码。
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
