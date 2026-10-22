"""独立采集命令使用的窗口；生产会话不加载采集器及其驱动生命周期。"""

import signal

from dev_tools.campaign.capture.reconstruct import DriverWindow

from module.campaign.tools import runtime, settings


class Window(DriverWindow):
    """原型使用的受控游戏窗口，全进程同时只允许一个。

    在扫描用的 DriverWindow 之上加了三件事：单实例约束（两个实例会互相抢鼠标）、
    输入经 GuardedInput 包装、注册中断处理让 Ctrl+C 也能走正常释放流程。
    构造过程中任何一步失败都会调用 close 释放已取得的驱动，不留下占着鼠标的残留实例。
    """

    def __init__(self, args=None):
        """建立单个活动控制窗口并安装中断处理，后续初始化失败也必须释放已取得的驱动。

        args 默认使用固定客户区及 ROI 设置，初始化时校验停止信号和单活动实例约束。
        记录客户区原点并注册中断处理；底层驱动创建后的后续初始化失败会进入 close 释放。
        """
        runtime.check_stop()
        if runtime._active is not None:
            raise RuntimeError('Another prototype window is already active')
        self._focused = False
        self._closed = False
        self._signals = {}
        super().__init__(args or settings.driver_args())
        try:
            self._origin = self.gui.ClientToScreen(self.hwnd, (0, 0))
            self.handler = runtime.GuardedInput(self, self.handler)
            runtime._active = self
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
        runtime.check_stop()
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
        if self._closed:
            return
        self._closed = True
        try:
            super().close()
        finally:
            runtime._active = None
            for sig, handler in self._signals.items():
                signal.signal(sig, handler)
