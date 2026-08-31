"""
HwndWindowAdapter：ok-script HwndWindow 窗口模型的最小适配版。

来源：ok/device/capture_methods/hwnd_window.py（ok-script 1.0.190, AGPL-3.0）。

ok 原版维护 hwnd / top_hwnd / hwnds（子窗口列表）三元窗口模型，
供 PostMessageInteraction 做坐标换算与动态目标 hwnd 判定。
本适配版用 win32gui 按当前项目的 Window（class_name + title）查找窗口：

- hwnd：由主窗口解析器验证后的 Window.hwnd
- top_hwnd：与 hwnd 相同（本项目无独立 top_hwnd_class 概念，
  top_offset 恒为 0，get_top_window_cords 为恒等映射）
- hwnds：EnumChildWindows 枚举的子窗口列表，
  结构与 ok 一致 [hwnd, '', width, height, left, top]（屏幕坐标），
  供点击按屏幕坐标命中实际渲染子窗口
"""
import win32gui

from module.logger import logger


class HwndWindowAdapter:
    def __init__(self, window_provider, hwnd_resolver):
        """
        Args:
            window_provider: () -> Window，延迟获取当前操作的窗口
                （Automation.current_window，Game/Launcher 会在运行期切换）
        """
        self._window_provider = window_provider
        self._hwnd_resolver = hwnd_resolver
        self.hwnd = 0
        self.top_hwnd = 0
        self.hwnds = []
        self.top_offset_x = 0
        self.top_offset_y = 0

    def update(self) -> bool:
        """按当前窗口信息刷新句柄，窗口不存在时返回 False"""
        window = self._window_provider()
        if window is None:
            return False
        try:
            hwnd = self._hwnd_resolver() or 0
            if hwnd and (
                win32gui.GetWindowText(hwnd) != window.title
                or win32gui.GetClassName(hwnd) != window.class_name
            ):
                hwnd = 0
        except Exception as e:
            logger.warning(f'Window resolver error: {e}')
            hwnd = 0
        if not hwnd:
            logger.warning(f'Window not found: [{window.name}]:[{window.title}]')
            return False
        if hwnd != self.hwnd:
            logger.info(f'HwndWindow updated: [{window.name}] hwnd {self.hwnd} -> {hwnd}')
        self.hwnd = hwnd
        self.top_hwnd = hwnd
        self.top_offset_x = 0
        self.top_offset_y = 0
        self.hwnds = self._enum_hwnds(hwnd)
        return True

    @staticmethod
    def _enum_hwnds(hwnd):
        hwnds = []

        def callback(child, _):
            if win32gui.IsWindowVisible(child):
                left, top, right, bottom = win32gui.GetWindowRect(child)
                hwnds.append([child, '', right - left, bottom - top, left, top])
            return True

        try:
            win32gui.EnumChildWindows(hwnd, callback, None)
        except Exception as e:
            logger.error(f'EnumChildWindows error: {e}')
        return hwnds

    def get_top_window_cords(self, x, y):
        # ok 原版：client 坐标 -> 顶层窗口坐标；本项目 top_offset 恒为 0
        return x - self.top_offset_x, y - self.top_offset_y

    def is_foreground(self) -> bool:
        try:
            foreground = win32gui.GetForegroundWindow()
        except Exception:
            return False
        if foreground == self.hwnd:
            return True
        return any(hwnd_info[0] == foreground for hwnd_info in self.hwnds)
