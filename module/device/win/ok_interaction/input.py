"""
PostMessageInput：ok 控制方案策略层，移植自 ok-bd2 的 BD2Interaction
（src/interaction/BD2Interaction.py，基于 ok-script PostMessageInteraction）。

与 module.device.win.input.Input 接口完全对齐（mouse_click / mouse_down /
mouse_up / mouse_move / mouse_scroll / mouse_swipe / press_mouse_click），
由 Automation._init_input() 按 PCClientInfo.ControlScheme 选择。

策略与现有 pyautogui 方案（全局物理输入）的区别：
- 点击：PostMessage 向目标窗口发送按下/抬起消息，配合 SetCursorPos
  聚焦物理光标（ok-bd2「正式版方案」），并按屏幕坐标动态定位实际
  接收消息的子窗口
- 滑动：真实光标沿路径移动，同时通过 PostMessage 发送后台拖拽消息；
  不调用 SetForegroundWindow
- 滚动：NIKKE 不消费后台投递的 WM_MOUSEWHEEL，转换为光标辅助的后台拖拽；
  与滑动相同，仅短暂占用鼠标，不切换真实前台窗口
- 横切：_input_lock 互斥；_operate 记录/恢复光标，BlockInput 期间
  屏蔽用户输入防止干扰；try_activate 为 PostMessage 假激活
  （WM_ACTIVATE 消息），不调用 SetForegroundWindow 抢占真实前台

坐标约定：与 Input 相同，方法入参为屏幕绝对坐标（Automation 已叠加
current_window.offset），内部换算为目标窗口客户区坐标。

启动器仍依赖前台输入，所有操作动态回退到 Input。
"""
import ctypes
import threading
import time

import win32api
import win32con
import win32gui

from module.device.win.input import Input
from module.device.win.ok_interaction.hwnd_window import HwndWindowAdapter
from module.device.win.ok_interaction.post_message import PostMessageInteraction
from module.logger import logger


class PostMessageInput(Input):
    def __init__(self, window_provider, hwnd_resolver):
        """
        Args:
            window_provider: () -> Window，延迟获取当前操作的窗口
        """
        super().__init__()
        self.hwnd_window = HwndWindowAdapter(window_provider, hwnd_resolver=hwnd_resolver)
        self.interaction = PostMessageInteraction(self.hwnd_window)
        self.cursor_position = None
        self._mouse_screen_position = None
        self._operating = False
        self._input_lock = threading.RLock()
        self.user32 = ctypes.windll.user32
        # ok 原版 mouse_up 读取的 mouse_pos 无更新链路，这里自行记录按下的 lParam
        self._last_down_pos = 0

    # ------------------------------------------------------------------
    # 横切机制（移植自 BD2Interaction）
    # ------------------------------------------------------------------
    def _operate(self, fun, block=False, restore_cursor=True):
        """互斥执行：记录光标原位，block 时 BlockInput，结束后恢复光标"""
        with self._input_lock:
            result = None
            is_outer_operate = False
            if not self._operating:
                self.cursor_position = win32api.GetCursorPos()
                self._operating = True
                is_outer_operate = True

            if block:
                self._block_input()
            try:
                result = fun()
            finally:
                if is_outer_operate:
                    self._operating = False
                    if restore_cursor:
                        self._restore_cursor()
                if block:
                    self._unblock_input()
            return result

    def _block_input(self):
        # 无管理员权限时 BlockInput 静默失败，不影响主流程
        try:
            self.user32.BlockInput(True)
        except Exception as e:
            logger.error(f'BlockInput error: {e}')

    def _unblock_input(self):
        try:
            self.user32.BlockInput(False)
        except Exception as e:
            logger.error(f'BlockInput error: {e}')

    def _restore_cursor(self):
        time.sleep(0.025)
        if self.cursor_position is None:
            return
        try:
            win32api.SetCursorPos(self.cursor_position)
        except Exception as e:
            logger.error(f'restore cursor error: {e}')

    def _ensure_window(self) -> bool:
        if not self.hwnd_window.update():
            logger.error(f'PostMessage control scheme: window not found, '
                         f'title [{self._window_title()}]')
            return False
        return True

    def _window_title(self):
        window = self.hwnd_window._window_provider()
        return getattr(window, 'title', '?') if window else '?'

    def _use_postmessage(self):
        window = self.hwnd_window._window_provider()
        return getattr(window, 'name', None) == 'Game'

    def _to_client(self, x, y):
        """屏幕绝对坐标 -> 基础窗口客户区坐标"""
        base_hwnd = self.hwnd_window.top_hwnd or self.hwnd_window.hwnd
        origin_x, origin_y = win32gui.ClientToScreen(base_hwnd, (0, 0))
        return x - origin_x, y - origin_y

    # ------------------------------------------------------------------
    # 点击（BD2Interaction 正式版方案：SetCursorPos + PostMessage）
    # ------------------------------------------------------------------
    def mouse_click(self, x, y):
        """在屏幕（x, y）位置点击：移动物理光标后向窗口发送按下/抬起消息"""
        if not self._use_postmessage():
            return super().mouse_click(x, y)
        self._operate(lambda: self._click(x, y, down_time=0.01), block=True, restore_cursor=True)

    def press_mouse_click(self, x, y, wait_time=0.2):
        """按住点击，按住时长由 wait_time 指定"""
        if not self._use_postmessage():
            return super().press_mouse_click(x, y, wait_time=wait_time)
        self._operate(lambda: self._click(x, y, down_time=wait_time), block=True, restore_cursor=True)

    def _click(self, x, y, down_time=0.01, key='left'):
        if not self._ensure_window():
            return
        try:
            self.interaction.try_activate()
            client_x, client_y = self._to_client(x, y)
            # update_mouse_pos 按屏幕坐标动态定位接收消息的子窗口，
            # 返回目标窗口局部坐标的 lParam
            click_pos = self.interaction.update_mouse_pos(client_x, client_y)
            win32api.SetCursorPos((int(x), int(y)))
            time.sleep(0.025)

            if key == 'left':
                btn_down, btn_mk, btn_up = win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, win32con.WM_LBUTTONUP
            elif key == 'middle':
                btn_down, btn_mk, btn_up = win32con.WM_MBUTTONDOWN, win32con.MK_MBUTTON, win32con.WM_MBUTTONUP
            else:
                btn_down, btn_mk, btn_up = win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, win32con.WM_RBUTTONUP
            self.interaction.post(btn_down, btn_mk, click_pos)
            time.sleep(down_time)
            self.interaction.post(btn_up, 0, click_pos)
            logger.debug(f'PostMessage click ({x}, {y})')
        except Exception as e:
            logger.error(f'PostMessage click error ({x}, {y}): {e}')

    def mouse_down(self, x, y):
        """在屏幕（x, y）位置按下鼠标按钮"""
        if not self._use_postmessage():
            return super().mouse_down(x, y)
        self._operate(lambda: self._mouse_down(x, y), block=False, restore_cursor=True)

    def _mouse_down(self, x, y):
        if not self._ensure_window():
            return
        try:
            self.interaction.try_activate()
            client_x, client_y = self._to_client(x, y)
            click_pos = self.interaction.update_mouse_pos(client_x, client_y)
            self._last_down_pos = click_pos
            win32api.SetCursorPos((int(x), int(y)))
            time.sleep(0.025)
            self.interaction.post(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, click_pos)
            logger.debug(f'PostMessage mouse down ({x}, {y})')
        except Exception as e:
            logger.error(f'PostMessage mouse down error ({x}, {y}): {e}')

    def mouse_up(self):
        """释放鼠标按钮"""
        if not self._use_postmessage():
            return super().mouse_up()
        try:
            self.interaction.post(win32con.WM_LBUTTONUP, 0, self._last_down_pos)
            logger.debug('PostMessage mouse up')
        except Exception as e:
            logger.error(f'PostMessage mouse up error: {e}')

    def mouse_move(self, x, y):
        """更新后台鼠标位置并发送 WM_MOUSEMOVE，不移动真实光标。"""
        if not self._use_postmessage():
            return super().mouse_move(x, y)
        if not self._ensure_window():
            return
        try:
            self.interaction.try_activate()
            client_x, client_y = self._to_client(x, y)
            self._mouse_screen_position = (int(x), int(y))
            self.interaction.move(client_x, client_y)
            logger.debug(f'PostMessage mouse move ({x}, {y})')
        except Exception as e:
            logger.error(f'PostMessage mouse move error ({x}, {y}): {e}')

    # ------------------------------------------------------------------
    # 滚动（使用光标辅助的后台拖拽）
    # ------------------------------------------------------------------
    def mouse_scroll(self, count, direction=-1, pause=True):
        """
        将滚动转换为短距离垂直拖拽，direction 正数向上、负数向下。
        真实光标只在受保护操作内移动，消息仍发送到后台游戏窗口。
        """
        if not self._use_postmessage():
            return super().mouse_scroll(count, direction=direction, pause=pause)
        self._operate(lambda: self._scroll(count, direction), block=True, restore_cursor=True)

    def _scroll(self, count, direction):
        if not self._ensure_window():
            return
        try:
            position = self._mouse_screen_position or win32api.GetCursorPos()
            distance = 65 * int(direction)
            for index in range(max(0, int(count))):
                self._postmessage_swipe(position, (position[0], position[1] + distance), 0.2)
                if index + 1 < count:
                    time.sleep(0.1)
            logger.debug(f'Cursor-assisted PostMessage scroll {count} x {direction}')
        except Exception as e:
            logger.error(f'PostMessage scroll error: {e}')

    # ------------------------------------------------------------------
    # 滑动（真实光标辅助的后台消息拖拽）
    # ------------------------------------------------------------------
    def mouse_swipe(self, p1, p2, speed=1.0):
        """
        使用真实光标位置辅助后台消息拖拽。

        NIKKE 的 Unity 输入层同时读取系统光标位置和窗口消息，因此
        仅发送 WM_MOUSEMOVE 不足以形成拖拽。操作期间屏蔽用户输入，
        光标沿路径移动，同时发送 WM_MOUSEMOVE/LBUTTON 消息；结束后
        自动恢复光标和用户输入。
        """
        if not self._use_postmessage():
            return super().mouse_swipe(p1, p2, speed=speed)

        distance_x, distance_y = p2[0] - p1[0], p2[1] - p1[1]
        distance = (distance_x**2 + distance_y**2) ** 0.5
        duration = min(max(distance / (100 * speed), 0.15), 0.7)

        self._operate(lambda: self._postmessage_swipe(p1, p2, duration), block=True, restore_cursor=True)
        logger.debug(f'Cursor-assisted PostMessage swipe ({p1[0]}, {p1[1]}) -> ({p2[0]}, {p2[1]})')

    def _postmessage_swipe(self, p1, p2, duration):
        """Perform one cursor-assisted drag inside an existing protected operation."""
        if not self._ensure_window():
            return
        steps = max(6, round(duration / 0.03))
        self.interaction.try_activate()
        start_client = self._to_client(int(p1[0]), int(p1[1]))
        start_pos = self.interaction.update_mouse_pos(*start_client)
        start_target = self.interaction.hwnd
        last_target = start_target
        last_pos = start_pos
        distance_x, distance_y = p2[0] - p1[0], p2[1] - p1[1]
        try:
            win32api.SetCursorPos((int(p1[0]), int(p1[1])))
            time.sleep(0.025)
            self.interaction.post(win32con.WM_MOUSEMOVE, 0, start_pos, hwnd=start_target)
            time.sleep(0.1)
            self.interaction.post(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, start_pos, hwnd=start_target)
            for index in range(1, steps + 1):
                ratio = index / steps
                screen_x = round(p1[0] + distance_x * ratio)
                screen_y = round(p1[1] + distance_y * ratio)
                client_x, client_y = self._to_client(screen_x, screen_y)
                move_pos = self.interaction.update_mouse_pos(client_x, client_y)
                target = self.interaction.hwnd
                win32api.SetCursorPos((screen_x, screen_y))
                self.interaction.post(
                    win32con.WM_MOUSEMOVE,
                    win32con.MK_LBUTTON,
                    move_pos,
                    hwnd=target,
                )
                last_target = target
                last_pos = move_pos
                time.sleep(duration / steps)
        finally:
            self.interaction.post(win32con.WM_LBUTTONUP, 0, last_pos, hwnd=last_target)
