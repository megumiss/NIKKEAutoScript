"""把生产 `Device` 包成移动闭环要的受控窗口。

已验证的镜头与移动闭环（`campaign.navigation` 的 `plan_world_move_compact`、`manual_move.navigate`）
是按采集器那套受控窗口写的：它要 `check` / `capture` / `focus` / `hwnd` / `gui` / `handler` /
`reset_minimap` 这几样。这里用任务调度的 `Device` 把同一组接口补齐，于是那套闭环不用改一行就能
跑在正式任务上，不必在生产侧重写第二份移动逻辑。
坐标约定：`handler` 收的是屏幕像素（与采集器一致），`Device.click_xy` / `drag_xy` 收的是客户区像素，
两者差一个客户区原点，换算只在这里做一次。
"""

import time

import numpy as np
import win32gui

from module.campaign.vision.chapter import CLIENT_SIZE
from module.campaign.vision.minimap import map_visible
from module.campaign.vision.observation import EXPANDED_ROI
from module.logger import logger


class WindowChanged(RuntimeError):
    """受控画面已经不可信：窗口被移动、改尺寸，或画面已不是会话预期的那一页。

    这些情况会使固定坐标与点击换算失效，或者让输入落到错误页面，必须停止当前会话。
    单纯失焦不属于此异常，由窗口层暂停输入并恢复前台后继续。
    """


class CampaignWindow:
    """移动闭环看到的「受控窗口」，底层是任务调度的 `Device`。

    会话期间客户区原点必须保持不变：原点一变，之前算出来的落点就全错位了，所以首次 check
    记下原点，之后每次 check 都比对。小地图开合不自己点，委托给持有者（`Campaign`）已经验证过的
    那套三态处理，避免同一组控件坐标在两处各写一份。
    """

    def __init__(self, owner, require_foreground=True):
        """owner 需提供 device、observer 与小地图开合方法（即 `Campaign` 实例）。

        require_foreground 为假时跳过前台校验，供离线回放与不抢焦点的诊断使用；
        现场移动必须保持为真，因为失焦时驱动的按键报告会被系统丢弃。
        """
        self.owner = owner
        self.device = owner.device
        self.require_foreground = require_foreground
        self.gui = win32gui
        self.handler = getattr(self.device, 'input_handler', None)
        self.origin = None
        self.handle = None
        self.trace = None

    @property
    def hwnd(self):
        """当前游戏窗口句柄；找不到窗口时为 0。"""
        return self.device.get_current_window_hwnd() or 0

    def client_origin(self):
        """客户区左上角在屏幕坐标里的位置；找不到窗口返回 None。"""
        rect = self.device.client_screen_rect()
        return None if rect is None else (rect[0], rect[1])

    def focus(self):
        """恢复游戏窗口前台并记下客户区原点；取焦等待期间仍响应任务停止。"""
        hwnd = self.hwnd
        if not hwnd:
            raise WindowChanged('找不到游戏窗口。')
        self.ensure_foreground(hwnd)
        self.handle = hwnd
        self.origin = self.client_origin()
        return self.origin is not None

    def ensure_foreground(self, hwnd):
        """失焦时暂停当前动作并循环恢复前台，成功后返回，不再因此终止主线。

        等待期间不发送场景输入，每 2 秒重试一次系统激活；停止回调每轮都会执行，
        因此任务取消不必等前台恢复。窗口消失或句柄更换仍属于不可恢复错误。
        """
        if not self.require_foreground or self.gui.GetForegroundWindow() == hwnd:
            return
        logger.warning('游戏窗口不在前台，暂停输入并等待恢复前台。')
        retry_at = 0.
        while self.gui.GetForegroundWindow() != hwnd:
            stop_check = getattr(self, 'stop_check', None)
            if stop_check is not None:
                stop_check()
            current = self.hwnd
            if not current:
                raise WindowChanged('游戏窗口已消失。')
            if current != hwnd:
                raise WindowChanged('游戏窗口句柄已变化，旧会话不可复用。')
            now = time.monotonic()
            if now >= retry_at:
                try:
                    self.device.set_foreground_window_with_retry(hwnd)
                except Exception as exc:
                    logger.warning(f'游戏窗口前台恢复失败，2 秒后重试：{exc}')
                retry_at = time.monotonic() + 2.
            if self.gui.GetForegroundWindow() != hwnd:
                time.sleep(0.2)
        logger.info('游戏窗口已恢复前台，继续执行主线。')

    def check(self):
        """核对窗口句柄、客户区尺寸和原点，并在失焦时等待恢复后继续。

        这是每次发输入前的闸门。尺寸、位置或窗口身份变化会使已算出的坐标失效，仍抛
        WindowChanged；单纯失焦不抛出，避免可恢复的切换直接终止整个任务。
        """
        stop_check = getattr(self, 'stop_check', None)
        if stop_check is not None:
            stop_check()
        hwnd = self.hwnd
        if not hwnd:
            raise WindowChanged('游戏窗口已消失。')
        if self.handle is None:
            self.handle = hwnd
        elif hwnd != self.handle:
            raise WindowChanged('游戏窗口句柄已变化，旧会话不可复用。')
        rect = self.gui.GetClientRect(hwnd)
        if (rect[2], rect[3]) != CLIENT_SIZE:
            raise WindowChanged(f'游戏画面尺寸变为 {rect[2]}×{rect[3]}（要求 1776×999），已算出的坐标失效。')
        origin = self.client_origin()
        if origin is None:
            raise WindowChanged('无法读取游戏窗口位置。')
        if self.origin is None:
            self.origin = origin
        elif origin != self.origin:
            raise WindowChanged(f'窗口被移动（{self.origin} → {origin}），点击换算已失效。')
        self.ensure_foreground(hwnd)

    def client(self):
        """最新一帧客户区 BGR 截图。"""
        self.check()
        self.device.screenshot()
        self.check()
        image = self.device.image
        if image is None or image.shape != (CLIENT_SIZE[1], CLIENT_SIZE[0], 3):
            raise WindowChanged('截图尺寸不是 1776×999，无法计算点击位置。')
        field = np.ascontiguousarray(image[:, :, ::-1])
        if self.trace is not None:
            self.trace.capture(field)
        return field

    def capture(self, require_map=True):
        """展开小地图面板的 ROI 截图（BGR）。

        require_map 为真时要求 ROI 看起来确实是地图面板，否则抛错：配准拿到一张不是地图的图
        只会给出离谱的定位，不如当场失败。
        """
        image = self.client()
        left, top, right, bottom = EXPANDED_ROI
        panel = np.ascontiguousarray(image[top:bottom, left:right])
        if require_map and not map_visible(panel):
            raise WindowChanged('展开地图面板不可见，无法用于定位。')
        return panel

    def reset_minimap(self, expanded=True, reset=True):
        """把小地图切到目标形态，委托给持有者已验证的开合流程。

        expanded 选目标态；reset 为真时要求回正视野，所以先确保紧凑态再展开——展开面板里
        平移过的视野不会自己回正，带着偏移去配准会把小队定位算到别处。
        关闭态先用左上角箭头重开，这一步由 reopen_minimap_if_closed 负责。
        """
        self.check()
        if self.trace is not None:
            self.trace.record('minimap_request', field=self.trace.last_field, force=True,
                              expanded=expanded, reset=reset)
        result = self.set_minimap_state(expanded=expanded, reset=reset)
        if self.trace is not None:
            image = self.device.image
            field = (np.ascontiguousarray(image[:, :, ::-1])
                     if isinstance(image, np.ndarray) and image.shape == (999, 1776, 3) else None)
            self.trace.record('minimap_result', field=field, force=True, mode=result)
        return result

    def set_minimap_state(self, expanded=True, reset=True):
        """执行既有地图三态切换，失败向上抛出；记录器只在外层复用操作前后的业务截图。"""
        callbacks = {}
        if getattr(self, 'input_check', None) is not None:
            callbacks = {'check': self.input_check, 'pause': self.pause}
        self.owner.reopen_minimap_if_closed(**callbacks)
        if expanded:
            if reset and self.owner.minimap_expanded():
                if not self.owner.collapse_minimap(**callbacks):
                    raise WindowChanged('展开地图回正前无法收起，不能沿用偏移视野。')
            if not self.owner.expand_minimap(**callbacks):
                raise WindowChanged('展开小地图失败。')
            return 'expanded'
        if not self.owner.collapse_minimap(**callbacks):
            raise WindowChanged('收起小地图失败。')
        return 'compact'

    def click_screen(self, x, y):
        """按屏幕坐标点一次，与采集器 handler.mouse_click 的坐标约定一致。"""
        self.check()
        origin = self.origin or self.client_origin()
        self.device.click_xy(int(round(x)) - origin[0], int(round(y)) - origin[1])

    def close(self):
        """会话收尾：释放窗口相关状态。

        生产 `Device` 由任务调度持有，这里不关它，只丢掉原点记录，
        下次会话重新取焦时再记——否则跨会话比对的是过期原点。
        """
        self.origin = None
        self.handle = None
