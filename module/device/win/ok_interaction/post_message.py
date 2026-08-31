"""
PostMessageInteraction：ok-script 1.0.190 PostMessage 交互机制的移植版。

来源：ok/device/interaction_methods/post_message.py（AGPL-3.0，
https://github.com/ok-oldking/ok-script）。

本文件是机制层：只负责消息发送、坐标换算、动态目标子窗口判定、
窗口激活消息，不包含具体操作策略（点击/拖拽/滚轮等
策略层见同目录 input.py）。

相对 ok 原版的适配：
- 去除 BaseInteraction 基类与 capture 依赖，__init__ 只接收 hwnd_window
- ok.util.logger -> module.logger
- 键盘方法（send_key / input_text / make_lparam）未移植
"""
import win32api
import win32con
import win32gui

from module.logger import logger


class PostMessageInteraction:
    def __init__(self, hwnd_window):
        self.hwnd_window = hwnd_window
        self._dynamic_target_hwnd = 0

    @property
    def hwnd(self):
        # 动态目标窗口有效则优先，否则回退 top_hwnd / hwnd
        if self._dynamic_target_hwnd != 0:
            if win32gui.IsWindow(self._dynamic_target_hwnd):
                return self._dynamic_target_hwnd
        return self.hwnd_window.top_hwnd if self.hwnd_window.top_hwnd else self.hwnd_window.hwnd

    def post(self, message, wParam=0, lParam=0, hwnd=None):
        """向目标窗口 PostMessage，失败仅记录日志不抛异常"""
        if hwnd is None:
            hwnd = self.hwnd
        try:
            win32gui.PostMessage(hwnd, message, wParam, lParam)
        except Exception as e:
            logger.error(f'PostMessage error {hwnd}: {e}')

    def move(self, x, y, down_btn=0):
        long_pos = self.update_mouse_pos(x, y, True)
        self.post(win32con.WM_MOUSEMOVE, down_btn, long_pos)
        return long_pos

    def activate(self, hwnd=None):
        self.post(win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0, hwnd=hwnd)

    def deactivate(self, hwnd=None):
        self.post(win32con.WM_ACTIVATE, win32con.WA_INACTIVE, 0, hwnd=hwnd)

    def try_activate(self):
        # PostMessage WM_ACTIVATE 消息假激活，不调用 SetForegroundWindow，
        # 不改变真实前台窗口；后台输入需要它让游戏接收消息
        base_hwnd = self.hwnd_window.hwnd
        current_hwnd = self.hwnd

        self.activate(base_hwnd)
        if current_hwnd != base_hwnd:
            self.activate(current_hwnd)

    def update_mouse_pos(self, x, y, activate=True):
        """
        client 坐标 -> 目标子窗口的局部坐标（MAKELONG lParam）。

        流程与 ok 原版一致：
        get_top_window_cords -> ClientToScreen(基础 hwnd) -> 在 hwnds
        子窗口矩形内按屏幕坐标命中目标 -> ScreenToClient(目标 hwnd)
        """
        if activate:
            self.try_activate()

        base_hwnd = self.hwnd_window.top_hwnd if self.hwnd_window.top_hwnd else self.hwnd_window.hwnd

        if x == -1 or y == -1:
            x, y = getattr(self, 'bg_mouse_pos', (0, 0))
        else:
            x, y = self.hwnd_window.get_top_window_cords(x, y)
            self.bg_mouse_pos = (x, y)

        try:
            abs_x, abs_y = win32gui.ClientToScreen(base_hwnd, (int(x), int(y)))

            target_hwnd = base_hwnd
            hwnds = getattr(self.hwnd_window, 'hwnds', [])
            for hwnd_info in hwnds:
                candidate = hwnd_info[0]
                if not win32gui.IsWindow(candidate):
                    continue
                try:
                    left = hwnd_info[4]
                    top = hwnd_info[5]
                    right = left + hwnd_info[2]
                    bottom = top + hwnd_info[3]
                    if left <= abs_x < right and top <= abs_y < bottom:
                        target_hwnd = candidate
                        break
                except Exception:
                    continue
            self._dynamic_target_hwnd = target_hwnd

            local_x, local_y = win32gui.ScreenToClient(target_hwnd, (abs_x, abs_y))
            return win32api.MAKELONG(local_x, local_y)

        except Exception as e:
            logger.error(f'update_mouse_pos conversion error targeting {base_hwnd}: {e}')
            self._dynamic_target_hwnd = base_hwnd
            return win32api.MAKELONG(int(x), int(y))
