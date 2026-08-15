"""
模板截取工具：替代 "第三方截图 + PS 选区" 的手工模板制作流程。

启动：
    python dev_tools/template_capture.py        命令行启动
    双击 dev_tools/template_capture.pyw          无控制台启动（pythonw）

流程（全程单个常驻窗口图形界面，不弹出额外窗口）：
    1. 常驻处理窗口：选择游戏窗口与屏幕（游戏窗口仅列出 NIKKE 国际服/港澳台服窗口；
       选择屏幕后立即将窗口调整为 720x1280 并居中，可选"跳过"）
    2. 点"截取窗口"（或按 F8 / C）截取窗口客户区，预览显示在窗口内，
       原始截图自动保存到"截图保存路径"
    3. 鼠标左键拖选选区，滚轮缩放，右键/中键拖动平移
    4. 选区完成后点击选区右下角的"保存按钮/保存模板"，
       弹出系统文件保存对话框选择路径与文件名（类似 PS 导出）

按键（处理窗口内，输入框聚焦时不生效）：
    F8 / C   截取窗口图像（并自动保存截图）
    B        保存为按钮模板（黑底反选涂黑，弹出保存对话框）
    T        保存为模板（裁剪选区，自动补 TEMPLATE_ 前缀，弹出保存对话框）
    R        重置缩放与选区
    Esc      退出
左侧截图列表：点击切换截图保存路径下的历史截图

自检：
    python dev_tools/template_capture.py --list   列出窗口与屏幕后退出
"""
import base64
import ctypes
import glob
import json
import re

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    pass

import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, ttk

import cv2
import imageio
import numpy as np
import psutil
import win32api
import win32con
import win32gui
import win32process
import win32ui

# 直接运行（python dev_tools/template_capture.py）时把仓库根目录加入模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pythonw（无控制台）下 stdout/stderr 为 None，重定向到 devnull 避免 logger 写入时崩溃
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

from module.base.utils import get_bbox, get_color
from module.device.win.app_control import GAME_PROCESS, GAME_TITLE
from module.logger import logger

try:
    import mss
except ModuleNotFoundError:
    mss = None

# 模板标准分辨率（与 button_extract / 运行时截图一致）
TARGET_WIDTH = 720
TARGET_HEIGHT = 1280

DEFAULT_SAVE_DIR = './pic'
CAPTURE_HOTKEY = '<f8>'
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.template_capture_config.json')

# 图像缩放范围（zoom=1 时为完整图像）
ZOOM_MIN = 1.0
ZOOM_MAX = 32.0
ZOOM_STEP = 1.25


def list_windows():
    """
    列出所有可见顶层窗口。

    Returns:
        list[dict]: hwnd, pid, title, class_name, process, client_size
    """
    results = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pid = 0
            process = '?'
        rect = win32gui.GetClientRect(hwnd)
        results.append({
            'hwnd': hwnd,
            'pid': pid,
            'title': title,
            'class_name': win32gui.GetClassName(hwnd),
            'process': process,
            'client_size': (rect[2], rect[3]),
        })

    win32gui.EnumWindows(callback, None)
    return results


def nikke_window_info(window):
    """
    复用项目 app_control 的 GAME_PROCESS / GAME_TITLE 定义，判断窗口是否为 NIKKE 游戏窗口。

    国际服:   nikke.exe / 标题 NIKKE
    港澳台服: nikke.exe / 标题 勝利女神：妮姬
    （两个服游戏进程名相同，需按窗口标题区分）

    Returns:
        tuple(server, title) or None: 匹配时返回 (服名, 游戏窗口标题)
    """
    if window['process'] not in set(GAME_PROCESS.values()):
        return None
    for server, title in GAME_TITLE.items():
        if window['title'] == title:
            return server, title
    return None


def is_nikke_window(window):
    """窗口是否为 NIKKE 游戏窗口（国际服或港澳台服）。"""
    return nikke_window_info(window) is not None


def list_monitors():
    """
    Returns:
        list[dict]: index, resolution, work_area, primary
    """
    monitors = []
    for i, (handle, _, _) in enumerate(win32api.EnumDisplayMonitors()):
        info = win32api.GetMonitorInfo(handle)
        left, top, right, bottom = info['Monitor']
        monitors.append({
            'index': i,
            'resolution': (right - left, bottom - top),
            'work_area': info['Work'],
            'primary': bool(info.get('Flags', 0) & win32con.MONITORINFOF_PRIMARY),
        })
    return monitors


def set_window_client(hwnd, client_width, client_height, screen_n=0):
    """
    将窗口客户区调整为指定分辨率，并在指定屏幕工作区内居中。
    与 module/device/win/game_control.py 的 change_resolution 同算法。
    """
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.5)

    client = win32gui.GetClientRect(hwnd)
    window = win32gui.GetWindowRect(hwnd)
    border_width = (window[2] - window[0] - client[2]) // 2
    border_height = (window[3] - window[1] - client[3]) // 2
    win_width = client_width + 2 * border_width
    win_height = client_height + 2 * border_height

    monitors = win32api.EnumDisplayMonitors()
    if not (0 <= screen_n < len(monitors)):
        logger.warning(f'Screen {screen_n} out of range, use screen 0')
        screen_n = 0
    work = win32api.GetMonitorInfo(monitors[screen_n][0])['Work']
    wl, wt, wr, wb = work

    x = wl + (wr - wl - win_width) // 2
    y = wt + (wb - wt - win_height) // 2
    x = max(wl, min(x, wr - win_width))
    y = max(wt, min(y, wb - win_height))

    try:
        win32gui.SetWindowPos(hwnd, 0, x, y, win_width, win_height, win32con.SWP_NOZORDER)
    except Exception as e:
        # 游戏以管理员权限运行时，普通权限进程无法调整其窗口（UIPI 限制）
        logger.warning(f'SetWindowPos failed: {e}（游戏窗口可能以管理员权限运行，'
                       f'请以管理员身份运行本工具，或手动将窗口调整为 {client_width}x{client_height}）')
        return
    time.sleep(0.3)

    rect = win32gui.GetClientRect(hwnd)
    if rect[2] != client_width or rect[3] != client_height:
        logger.warning(f'Client size: expected {client_width}x{client_height}, actual {rect[2]}x{rect[3]}')
    else:
        logger.info(f'Client resolution set to {client_width}x{client_height} on screen {screen_n}')


def _capture_printwindow(hwnd, client_width, client_height):
    """PrintWindow 截取客户区，窗口被遮挡时也能截取。"""
    win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(hwnd)
    win_width = win_right - win_left
    win_height = win_bottom - win_top
    client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
    crop_x = client_left - win_left
    crop_y = client_top - win_top

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, win_width, win_height)
    save_dc.SelectObject(bitmap)

    try:
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0x00000002)  # PW_RENDERFULLCONTENT
        if result != 1:
            result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)
        if result != 1:
            raise RuntimeError('PrintWindow failed')

        bmp_info = bitmap.GetInfo()
        bmp_data = bitmap.GetBitmapBits(True)
        full = np.frombuffer(bmp_data, dtype=np.uint8).reshape((bmp_info['bmHeight'], bmp_info['bmWidth'], 4))
        full = full[:, :, :3][:, :, ::-1]  # BGRA -> RGB

        image = full[crop_y:crop_y + client_height, crop_x:crop_x + client_width]
        if image.size == 0:
            raise RuntimeError('PrintWindow crop is empty')
        return image
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def _capture_mss(hwnd, client_width, client_height):
    """mss 屏幕截取，要求窗口客户区在屏幕上可见。"""
    if mss is None:
        raise ModuleNotFoundError('mss is not installed')
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    with mss.MSS() as sct:
        shot = sct.grab({'left': left, 'top': top, 'width': client_width, 'height': client_height})
        image = np.asarray(shot)[:, :, :3]  # BGR
        return image[:, :, ::-1].copy()  # RGB


def capture_client(hwnd, method='printwindow'):
    """
    截取窗口客户区，并按运行时截图规则对齐分辨率（宽于 TARGET_WIDTH 时按宽度比例缩放）。

    Returns:
        np.ndarray: RGB image
    """
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.5)

    rect = win32gui.GetClientRect(hwnd)
    client_width, client_height = rect[2], rect[3]
    if client_width <= 0 or client_height <= 0:
        raise RuntimeError('Window client area is empty')

    if method == 'printwindow':
        try:
            image = _capture_printwindow(hwnd, client_width, client_height)
        except Exception as e:
            logger.warning(f'PrintWindow failed: {e}, fallback to mss (window must be visible)')
            image = _capture_mss(hwnd, client_width, client_height)
    else:
        image = _capture_mss(hwnd, client_width, client_height)

    # 与 Screenshot.take_screenshot 一致的缩放规则
    if client_width > TARGET_WIDTH:
        factor = TARGET_WIDTH / client_width
        new_size = (int(client_width * factor), int(client_height * factor))
        image = cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)
        logger.info(f'Scaled screenshot: {client_width}x{client_height} -> {new_size[0]}x{new_size[1]}')

    h, w = image.shape[:2]
    if (w, h) != (TARGET_WIDTH, TARGET_HEIGHT):
        logger.warning(f'Screenshot size {w}x{h} != {TARGET_WIDTH}x{TARGET_HEIGHT}, '
                       f'button_extract will warn on this template')
    return image


def show_error(title, message):
    """错误弹窗，无控制台环境下也能看到失败原因。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        messagebox.showerror(title, message, parent=root)
        root.destroy()
    except Exception:
        print(f'{title}: {message}')


def save_png(file, image):
    folder = os.path.dirname(file)
    if folder:
        os.makedirs(folder, exist_ok=True)
    imageio.imwrite(file, image)
    logger.info(f'Saved: {file}')


def load_config():
    """读取工具配置（左侧面板设置持久化）。"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f'Save config failed: {e}')


class TemplateCaptureApp:
    """常驻处理窗口：选窗口 / 选屏幕 / 截取 / 选区 / 保存全部在同一个窗口内完成。"""

    def __init__(self, capture_method='printwindow'):
        self.capture_method = capture_method
        self.image = None        # RGB numpy
        self.zoom = ZOOM_MIN
        self.view_x = 0.0
        self.view_y = 0.0
        self.win_size = (1, 1)   # 预览显示尺寸 (w, h)
        self.sel_start = None
        self.sel_end = None
        self.selecting = False
        self.panning = False
        self.pan_anchor = None
        self.mouse = (0, 0)
        self._render_pending = False    # 高频渲染合并（滚轮/平移防抖）
        self._render_low_res = False    # 待渲染帧是否低分辨率
        self._render_job = None         # 防抖渲染任务句柄
        self._full_render_job = None    # 滚动停止后的全尺寸渲染任务
        self._photo = None       # 保持 PhotoImage 引用
        self.listener = None
        self.windows = []
        self.monitors = []
        self.hwnd = None
        self.screen_n = 0        # None 表示跳过屏幕调整
        self.last_save_dir = DEFAULT_SAVE_DIR  # 保存对话框初始目录

        # canvas 图形项引用（增量更新用）
        self.sel_rect = None
        self.sel_label = None
        self.cross_h = None
        self.cross_v = None
        self.info_item = None
        self.save_btn = None    # 选区右下角浮动保存按钮
        self._hover_win = None  # 悬浮缩略图窗口
        self._hover_img = None
        self._hover_lbl = None
        self._hover_path = None
        self._hover_cache = {}  # path -> PhotoImage 缩略图缓存

        self.root = tk.Tk()
        self.root.title('Template Capture')
        # 窗口尺寸：高度固定，宽度自适应竖图（画布与图像同宽，无空白）
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_h = min(int(1200 * 1.5), sh - 60)
        canvas_h = win_h - 45  # 减去状态栏与上下边距的估算
        image_w = int(canvas_h * TARGET_WIDTH / TARGET_HEIGHT)
        win_w = min(400 + 16 + image_w, sw - 40)
        x = max((sw - win_w) // 2, 0)
        y = max((sh - win_h) // 2 - 30, 0)
        self.root.geometry(f'{win_w}x{win_h}+{x}+{y}')
        self.build_ui()
        self.refresh_windows()
        self.refresh_monitors()
        self.config_data = load_config()
        self.apply_saved_settings()
        self.refresh_capture_list()
        self.root.protocol('WM_DELETE_WINDOW', self.quit_app)
        self.root.bind('<KeyPress>', self.on_key)

    # ---------- UI ----------

    def build_ui(self):
        # 状态栏（先 pack，占底部全宽）
        self.status_var = tk.StringVar(value='请选择游戏窗口与屏幕，然后点击"截取窗口"')
        tk.Label(self.root, textvariable=self.status_var, anchor='w', padx=10, pady=4, fg='#333').pack(
            side=tk.BOTTOM, fill=tk.X)

        # ---------- 左列：控制面板 ----------
        panel = tk.Frame(self.root, width=400)
        panel.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 6), pady=(10, 4))
        panel.pack_propagate(False)

        # 游戏窗口
        tk.Label(panel, text='游戏窗口:').grid(row=0, column=0, sticky='w')
        tk.Button(panel, text='刷新', width=6, command=self.refresh_windows).grid(row=0, column=1, sticky='e')
        self.win_combo = ttk.Combobox(panel, state='readonly')
        self.win_combo.grid(row=1, column=0, columnspan=2, sticky='we', pady=(0, 8))
        self.win_combo.bind('<<ComboboxSelected>>', self.on_window_selected)

        # 屏幕（选择后立即调整窗口为 720x1280 并居中）
        tk.Label(panel, text='屏幕 (选后调至 720x1280 居中):').grid(row=2, column=0, columnspan=2, sticky='w')
        self.mon_combo = ttk.Combobox(panel, state='readonly')
        self.mon_combo.grid(row=3, column=0, columnspan=2, sticky='we', pady=(0, 8))
        self.mon_combo.bind('<<ComboboxSelected>>', self.on_screen_selected)

        # 截图保存路径（每次截取自动保存原始截图）
        tk.Label(panel, text='截图保存路径:').grid(row=4, column=0, columnspan=2, sticky='w')
        self.capture_dir_var = tk.StringVar(value=DEFAULT_SAVE_DIR)
        self.capture_dir_entry = tk.Entry(panel, textvariable=self.capture_dir_var)
        self.capture_dir_entry.grid(row=5, column=0, sticky='we', pady=(0, 8))
        tk.Button(panel, text='浏览...', width=8, command=self.browse_capture_dir).grid(
            row=5, column=1, sticky='e', pady=(0, 8), padx=(4, 0))

        # 截图列表（点击切换预览，最新在前）
        tk.Label(panel, text='截图列表 (点击切换):').grid(row=6, column=0, columnspan=2, sticky='w')
        cap_frame = tk.Frame(panel)
        cap_frame.grid(row=7, column=0, columnspan=2, sticky='nsew', pady=(0, 8))
        self.cap_list = tk.Listbox(cap_frame, font=('Consolas', 10), activestyle='none')
        cap_scroll = tk.Scrollbar(cap_frame, orient=tk.VERTICAL, command=self.cap_list.yview)
        self.cap_list.configure(yscrollcommand=cap_scroll.set)
        self.cap_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cap_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.cap_list.bind('<<ListboxSelect>>', self.on_capture_selected)
        self.cap_list.bind('<Motion>', self.on_cap_list_motion)
        self.cap_list.bind('<Leave>', self.on_cap_list_leave)
        panel.rowconfigure(7, weight=1)

        # 操作按钮（竖排）
        for i, (text, cmd) in enumerate([
            ('截取窗口 (F8/C)', self.do_capture),
            ('重置选区 (R)', self.reset_view),
            ('退出 (Esc)', self.quit_app),
        ]):
            tk.Button(panel, text=text, width=16, command=cmd).grid(
                row=8 + i, column=0, columnspan=2, sticky='we', pady=2)

        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)

        # ---------- 右列：截图预览画布 ----------
        canvas_frame = tk.Frame(self.root)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 10), pady=(10, 4))
        self.canvas = tk.Canvas(canvas_frame, bg='#1e1e1e', highlightthickness=1, highlightbackground='#555')
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind('<Configure>', self.on_canvas_resize)
        self.canvas.bind('<Motion>', self.on_mouse_move)
        self.canvas.bind('<ButtonPress-1>', self.on_mouse_down)
        self.canvas.bind('<B1-Motion>', self.on_mouse_move)
        self.canvas.bind('<ButtonRelease-1>', self.on_mouse_up)
        self.canvas.bind('<ButtonPress-3>', self.on_pan_start)
        self.canvas.bind('<B3-Motion>', self.on_mouse_move)
        self.canvas.bind('<ButtonRelease-3>', self.on_pan_end)
        self.canvas.bind('<ButtonPress-2>', self.on_pan_start)
        self.canvas.bind('<B2-Motion>', self.on_mouse_move)
        self.canvas.bind('<ButtonRelease-2>', self.on_pan_end)
        self.canvas.bind('<MouseWheel>', self.on_mousewheel)

    # ---------- 窗口 / 屏幕选择 ----------

    def refresh_windows(self):
        self.windows = [w for w in list_windows() if is_nikke_window(w)]
        entries = []
        for w in self.windows:
            server, title = nikke_window_info(w)
            entries.append(f'{title} ({server}) | PID {w["pid"]} | {w["client_size"][0]}x{w["client_size"][1]}')
        self.win_combo['values'] = entries
        if self.windows:
            self.win_combo.current(0)
            self.hwnd = self.windows[0]['hwnd']
            self.status(f'已找到 {len(self.windows)} 个 NIKKE 窗口（国际服/港澳台服），默认选中第一个，点击"截取窗口"开始')
        else:
            self.hwnd = None
            self.status('未找到 NIKKE 窗口，请确认游戏已打开后点击"刷新"')

    def refresh_monitors(self):
        self.monitors = list_monitors()
        values = [
            f'屏幕 {m["index"]}: {m["resolution"][0]}x{m["resolution"][1]}'
            f'{" (主屏)" if m["primary"] else ""}'
            for m in self.monitors
        ]
        values.append('跳过（不调整窗口）')
        self.mon_combo['values'] = values
        if self.monitors:
            self.mon_combo.current(0)
            self.screen_n = 0
        else:
            self.mon_combo.current(len(values) - 1)
            self.screen_n = None

    def on_window_selected(self, event=None):
        idx = self.win_combo.current()
        if 0 <= idx < len(self.windows):
            self.hwnd = self.windows[idx]['hwnd']
            self.status(f'已选择窗口: {self.windows[idx]["title"]}')

    def on_screen_selected(self, event=None):
        idx = self.mon_combo.current()
        self.screen_n = idx if 0 <= idx < len(self.monitors) else None
        if self.screen_n is not None:
            self.apply_window_setup()
            self.status(f'已将窗口调整到屏幕 {self.screen_n}（720x1280 居中）')
        else:
            self.status('已选择跳过，窗口大小不做调整')

    def apply_window_setup(self):
        if self.hwnd is None or self.screen_n is None:
            return
        set_window_client(self.hwnd, TARGET_WIDTH, TARGET_HEIGHT, screen_n=self.screen_n)

    # ---------- 设置持久化 ----------

    def apply_saved_settings(self):
        """恢复上次保存的左侧面板设置：窗口（按服）、屏幕、截图保存路径。"""
        cfg = self.config_data

        if cfg.get('capture_dir'):
            self.capture_dir_var.set(cfg['capture_dir'])

        # 屏幕：saved 为 None 表示"跳过"
        if 'screen' in cfg:
            saved = cfg['screen']
            if saved is None:
                self.mon_combo.current(len(self.monitors))
                self.screen_n = None
            elif 0 <= saved < len(self.monitors):
                self.mon_combo.current(saved)
                self.screen_n = saved

        # 窗口：按服匹配（国际服/港澳台服双开也能恢复正确的那个）
        saved_server = cfg.get('server')
        if saved_server:
            for i, w in enumerate(self.windows):
                info = nikke_window_info(w)
                if info and info[0] == saved_server:
                    self.win_combo.current(i)
                    self.hwnd = w['hwnd']
                    break

    def collect_settings(self):
        """收集左侧面板当前设置，供退出时保存。"""
        cfg = {}
        idx = self.win_combo.current()
        if 0 <= idx < len(self.windows):
            info = nikke_window_info(self.windows[idx])
            if info:
                cfg['server'] = info[0]
        midx = self.mon_combo.current()
        cfg['screen'] = midx if 0 <= midx < len(self.monitors) else None
        cfg['capture_dir'] = self.capture_dir_var.get().strip() or DEFAULT_SAVE_DIR
        return cfg

    # ---------- 截取与预览 ----------

    def do_capture(self):
        if self.hwnd is None:
            self.status('请先选择游戏窗口')
            return
        self.apply_window_setup()
        logger.info('Capturing window...')
        try:
            self.image = capture_client(self.hwnd, method=self.capture_method)
        except Exception as e:
            logger.error(f'Capture failed: {e}')
            self.status(f'截取失败: {e}')
            return
        self.reset_view()
        h, w = self.image.shape[:2]
        saved = self.save_capture_raw()
        if saved:
            self.status(f'已截取 {w}x{h}（自动保存: {saved}），左键拖选选区，B/T 保存')
        else:
            self.status(f'已截取 {w}x{h}，左键拖选选区，滚轮缩放，B/T 保存')
        self.fit_view()
        self.render()
        self.refresh_capture_list()  # 新截图加入左侧列表
        # 截取后校准窗口宽度，消除画布水平空白
        self.root.after(50, self._calibrate_width)

    def save_capture_raw(self):
        """每次截取后把原始截图保存到"截图保存路径"，返回文件路径或 None。"""
        folder = self.capture_dir_var.get().strip().rstrip('/\\') or DEFAULT_SAVE_DIR
        ts = time.strftime('%Y%m%d_%H%M%S')
        file = os.path.join(folder, f'capture_{ts}.png')
        save_png(file, self.image)
        return file

    def browse_capture_dir(self):
        """弹出目录选择框，设置截图保存路径并刷新截图列表。"""
        folder = filedialog.askdirectory(
            title='选择截图保存路径',
            initialdir=self.capture_dir_var.get().strip() or DEFAULT_SAVE_DIR)
        if folder:
            self.capture_dir_var.set(folder)
            self.refresh_capture_list()

    def _calibrate_width(self):
        """窗口宽度自适应：收缩到画布宽度等于图像显示宽度，消除水平空白。"""
        cw = self.canvas.winfo_width()
        ww = self.win_size[0]
        if self.image is None or ww <= 0 or cw <= ww:
            return
        gap = cw - ww
        match = re.match(r'(\d+)x(\d+)', self.root.geometry())
        if not match:
            return
        new_w = max(int(match.group(1)) - gap, 200)
        self.root.geometry(f'{new_w}x{match.group(2)}')

    def fit_view(self):
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10 or self.image is None:
            return
        img_h, img_w = self.image.shape[:2]
        scale = min(cw / img_w, ch / img_h)
        self.win_size = (max(int(img_w * scale), 1), max(int(img_h * scale), 1))

    def on_canvas_resize(self, event=None):
        if self.image is None:
            self.render()  # 重画居中提示
            return
        self.fit_view()
        self.render()

    # ---------- 视图换算 ----------

    def view_scale(self):
        img_w = self.image.shape[1]
        view_w = img_w / self.zoom
        return self.win_size[0] / view_w

    def view_offset(self):
        """图像显示区域在画布中的左上角偏移（居中显示，空白均匀分布）。"""
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        return max((cw - self.win_size[0]) // 2, 0), max((ch - self.win_size[1]) // 2, 0)

    def to_image(self, mx, my):
        scale = self.view_scale()
        ox, oy = self.view_offset()
        img_h, img_w = self.image.shape[:2]
        ix = self.view_x + (mx - ox) / scale
        iy = self.view_y + (my - oy) / scale
        return min(max(ix, 0), img_w), min(max(iy, 0), img_h)

    def to_window(self, ix, iy):
        scale = self.view_scale()
        ox, oy = self.view_offset()
        return (ix - self.view_x) * scale + ox, (iy - self.view_y) * scale + oy

    def clamp_view(self):
        img_h, img_w = self.image.shape[:2]
        view_w = img_w / self.zoom
        view_h = img_h / self.zoom
        self.view_x = min(max(self.view_x, 0.0), max(img_w - view_w, 0.0))
        self.view_y = min(max(self.view_y, 0.0), max(img_h - view_h, 0.0))

    def selection(self):
        """
        Returns:
            (x0, y0, x1, y1) or None: 图像坐标整数选区
        """
        if self.sel_start is None or self.sel_end is None:
            return None
        x0, y0 = self.sel_start
        x1, y1 = self.sel_end
        x0, x1 = sorted((int(round(x0)), int(round(x1))))
        y0, y1 = sorted((int(round(y0)), int(round(y1))))
        img_h, img_w = self.image.shape[:2]
        x0, x1 = max(x0, 0), min(x1, img_w)
        y0, y1 = max(y0, 0), min(y1, img_h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return x0, y0, x1, y1

    def reset_view(self):
        self.zoom = ZOOM_MIN
        self.view_x = 0.0
        self.view_y = 0.0
        self.sel_start = None
        self.sel_end = None
        if self.image is not None:
            self.render()

    # ---------- 鼠标交互 ----------

    def on_mouse_down(self, event):
        if self.image is None:
            return
        self.selecting = True
        self.sel_start = self.to_image(event.x, event.y)
        self.sel_end = self.sel_start
        if self.save_btn is not None:
            self.save_btn.place_forget()  # 开始新选区时隐藏旧保存按钮
        self.update_cursor()

    def on_mouse_up(self, event):
        self.selecting = False
        # 松开后更新选区右下角的保存按钮
        self._update_save_buttons()

    def _update_save_buttons(self):
        """选区有效时在选区右下角浮动显示保存按钮，否则隐藏。"""
        sel = self.selection()
        if not sel:
            if self.save_btn is not None:
                self.save_btn.place_forget()
            return
        if self.save_btn is None:
            self.save_btn = tk.Frame(self.canvas)
            tk.Button(self.save_btn, text='保存按钮', width=10,
                      command=lambda: self.save('button')).pack(side=tk.LEFT, padx=1)
            tk.Button(self.save_btn, text='保存模板', width=10,
                      command=lambda: self.save('template')).pack(side=tk.LEFT, padx=1)
            self.save_btn.update_idletasks()  # 立即计算按钮实际尺寸，供位置 clamp 使用
        sx1, sy1 = self.to_window(sel[2], sel[3])
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        bw = self.save_btn.winfo_reqwidth()
        bh = self.save_btn.winfo_reqheight()
        x = min(sx1 + 8, max(cw - bw - 4, 0))
        y = min(sy1 + 8, max(ch - bh - 4, 0))
        self.save_btn.place(x=int(x), y=int(y))

    def refresh_capture_list(self):
        """扫描截图保存路径，刷新左侧截图列表（最新在前）。"""
        folder = self.capture_dir_var.get().strip().rstrip('/\\') or DEFAULT_SAVE_DIR
        self.capture_files = sorted(glob.glob(os.path.join(folder, 'capture_*.png')),
                                    key=os.path.getmtime, reverse=True)
        self.cap_list.delete(0, tk.END)
        for f in self.capture_files:
            ts = time.strftime('%m-%d %H:%M:%S', time.localtime(os.path.getmtime(f)))
            self.cap_list.insert(tk.END, f'{os.path.basename(f)}  ({ts})')
        # 文件列表变化，清空缩略图缓存
        self._hover_cache.clear()
        self._hide_hover()

    def on_capture_selected(self, event=None):
        """点击左侧截图列表项 → 加载该截图到预览。"""
        sel = self.cap_list.curselection()
        if sel and 0 <= sel[0] < len(self.capture_files):
            self._hide_hover()
            self._load_capture_file(self.capture_files[sel[0]])

    # ---------- 悬浮缩略图预览 ----------

    def _ensure_hover_window(self):
        """创建悬浮预览窗口（无边框、置顶，默认隐藏）。"""
        if self._hover_win is not None:
            return
        self._hover_win = tk.Toplevel(self.root)
        self._hover_win.overrideredirect(True)
        self._hover_win.attributes('-topmost', True)
        self._hover_img = tk.Label(self._hover_win, bg='#222')
        self._hover_lbl = tk.Label(self._hover_win, bg='#222', fg='#ddd',
                                   font=('Consolas', 9), padx=4, pady=2)
        self._hover_img.pack()
        self._hover_lbl.pack(fill=tk.X)
        self._hover_win.withdraw()

    def _load_thumbnail(self, path):
        """加载截图缩略图（宽 720，即与原始截图同尺寸全清晰），用于悬浮预览。"""
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        tw = 720
        th = max(int(h * tw / w), 1)
        img = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode('.png', img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if not ok:
            return None
        return tk.PhotoImage(data=base64.b64encode(buf.tobytes()).decode('ascii'))

    def on_cap_list_motion(self, event):
        """鼠标在列表上移动：悬浮显示当前项截图的缩略图。"""
        if not self.cap_list.winfo_ismapped():
            return
        idx = self.cap_list.nearest(event.y)
        if not (0 <= idx < len(self.capture_files)):
            self._hide_hover()
            return
        path = self.capture_files[idx]
        if path == self._hover_path:
            self._move_hover(event)  # 项未变，仅跟随鼠标移动
            return
        photo = self._hover_cache.get(path)
        if photo is None:
            photo = self._load_thumbnail(path)
            if photo is None:
                self._hide_hover()
                return
            self._hover_cache[path] = photo
        self._ensure_hover_window()
        self._hover_img.configure(image=photo)
        self._hover_lbl.configure(text=os.path.basename(path))
        self._hover_path = path
        self._hover_win.deiconify()
        self._move_hover(event)

    def _move_hover(self, event):
        """悬浮窗跟随鼠标，显示在列表右侧（越界时移到左侧）。"""
        if self._hover_win is None:
            return
        x = self.cap_list.winfo_rootx() + self.cap_list.winfo_width() + 6
        y = event.y_root - 40
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = self._hover_win.winfo_reqwidth()
        h = self._hover_win.winfo_reqheight()
        if x + w > sw:
            x = max(self.cap_list.winfo_rootx() - w - 6, 0)
        if y + h > sh:
            y = max(sh - h, 0)
        self._hover_win.geometry(f'+{x}+{y}')

    def on_cap_list_leave(self, event):
        self._hide_hover()

    def _hide_hover(self):
        self._hover_path = None
        if self._hover_win is not None:
            self._hover_win.withdraw()

    def _load_capture_file(self, path):
        """加载一张已保存的截图到预览，可继续选区/保存。"""
        # cv2.imread 在 Windows 下不支持中文路径，用 np.fromfile + imdecode 替代
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)  # BGR
        if img is None:
            self.status(f'无法读取图片: {path}')
            return
        self.image = img[:, :, ::-1].copy()  # BGR -> RGB
        self.reset_view()
        h, w = self.image.shape[:2]
        self.status(f'已加载截图: {os.path.basename(path)} ({w}x{h})')
        self.fit_view()
        self.render()

    def on_pan_start(self, event):
        if self.image is None:
            return
        self.panning = True
        self.pan_anchor = (self.view_x, self.view_y, event.x, event.y)

    def on_pan_end(self, event):
        self.panning = False
        if self.image is not None:
            self._cancel_pending_render()
            self.render(low_res=False)

    def on_mouse_move(self, event):
        self.mouse = (event.x, event.y)
        if self.image is None:
            return
        if self.selecting:
            self.sel_end = self.to_image(event.x, event.y)
            sel = self.selection()
            if sel:
                if self.sel_rect is None:
                    # 首次出现有效选区：全量渲染创建选区框（按下时未渲染，需在此创建）
                    self.render()
                else:
                    sx0, sy0 = self.to_window(sel[0], sel[1])
                    sx1, sy1 = self.to_window(sel[2], sel[3])
                    self.canvas.coords(self.sel_rect, sx0, sy0, sx1, sy1)
                    label = f'({sel[0]},{sel[1]})-({sel[2]},{sel[3]}) {sel[2] - sel[0]}x{sel[3] - sel[1]}'
                    self.canvas.itemconfig(self.sel_label, text=label)
                    self.canvas.coords(self.sel_label, sx0, max(sy0 - 6, 12))
            self.update_cursor()
        elif self.panning:
            scale = self.view_scale()
            self.view_x = self.pan_anchor[0] - (event.x - self.pan_anchor[2]) / scale
            self.view_y = self.pan_anchor[1] - (event.y - self.pan_anchor[3]) / scale
            self.clamp_view()
            self._render_debounced(low_res=True)
        else:
            self.update_cursor()

    def on_mousewheel(self, event):
        """滚轮即时缩放（以光标为锚点），滚动中用半分辨率快速渲染，停止后恢复全尺寸。"""
        if self.image is None:
            return
        ix, iy = self.to_image(event.x, event.y)
        if event.delta > 0:
            self.zoom = min(self.zoom * ZOOM_STEP, ZOOM_MAX)
        else:
            self.zoom = max(self.zoom / ZOOM_STEP, ZOOM_MIN)
        scale = self.view_scale()
        ox, oy = self.view_offset()
        self.view_x = ix - (event.x - ox) / scale
        self.view_y = iy - (event.y - oy) / scale
        self.clamp_view()
        self._render_debounced(low_res=True)
        # 滚动停止后恢复全尺寸精细渲染
        if self._full_render_job is not None:
            self.root.after_cancel(self._full_render_job)
        self._full_render_job = self.root.after(150, self._render_full)

    def _render_full(self):
        self._full_render_job = None
        self._cancel_pending_render()
        self.render(low_res=False)

    def _render_debounced(self, low_res=False):
        """合并高频渲染请求：滚动/拖动中只保留最新一帧，且用低分辨率加速。"""
        if self._render_pending:
            self._render_low_res = self._render_low_res and low_res
            return
        self._render_pending = True
        self._render_low_res = low_res
        self._render_job = self.root.after(15, self._flush_render)

    def _flush_render(self):
        self._render_pending = False
        self.render(low_res=self._render_low_res)

    def _cancel_pending_render(self):
        """取消尚未执行的防抖渲染（用于平移结束/全尺寸恢复前）。"""
        if self._render_pending:
            self.root.after_cancel(self._render_job)
            self._render_pending = False

    def update_cursor(self):
        """增量更新十字线与坐标信息（含光标位置像素颜色），避免整帧重绘。"""
        if self.image is None or self.cross_h is None:
            return
        mx, my = self.mouse
        ox, oy = self.view_offset()
        self.canvas.coords(self.cross_h, ox, my, ox + self.win_size[0], my)
        self.canvas.coords(self.cross_v, mx, oy, mx, oy + self.win_size[1])
        ix, iy = self.to_image(mx, my)
        img_h, img_w = self.image.shape[:2]
        px, py = min(int(ix), img_w - 1), min(int(iy), img_h - 1)
        r, g, b = (int(v) for v in self.image[py, px])
        self.canvas.itemconfig(self.info_item,
                               text=f'zoom x{self.zoom:.2f} ({int(ix)},{int(iy)}) RGB({r},{g},{b})')

    # ---------- 渲染 ----------

    def render(self, low_res=False):
        """全量渲染预览。low_res=True 时编码用半分辨率、显示放大回全尺寸
        （滚动/拖动中提速，且画面尺寸不缩小）。"""
        if self.image is None:
            self.canvas.delete('all')
            self.canvas.create_text(
                self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2,
                text='尚未截图：选择窗口后点击"截取窗口"', fill='#888', font=('Microsoft YaHei UI', 12))
            return

        # canvas 尚未布局完成时（win_size 未 fit）先补一次适配
        if self.win_size == (1, 1):
            self.fit_view()
            if self.win_size == (1, 1):
                return

        self._render_inner(low_res)

    def _render_inner(self, low_res=False):
        img_h, img_w = self.image.shape[:2]
        view_w = img_w / self.zoom
        view_h = img_h / self.zoom
        x0 = int(self.view_x)
        y0 = int(self.view_y)
        x1 = min(int(np.ceil(self.view_x + view_w)), img_w)
        y1 = min(int(np.ceil(self.view_y + view_h)), img_h)
        crop = self.image[y0:y1, x0:x1]

        # 低分辨率模式：编码尺寸减半（提速），显示时整数放大回全尺寸
        target = self.win_size
        if low_res:
            target = (max(target[0] // 2, 1), max(target[1] // 2, 1))
        view = cv2.resize(crop, target, interpolation=cv2.INTER_NEAREST)
        # PNG 压缩级别降到 1（无损，编码比默认快数倍）；Tk PhotoImage 不支持 JPEG data
        ok, buf = cv2.imencode('.png', view[:, :, ::-1], [cv2.IMWRITE_PNG_COMPRESSION, 1])  # RGB -> BGR
        if not ok:
            return
        self._photo = tk.PhotoImage(data=base64.b64encode(buf.tobytes()).decode('ascii'))
        if low_res:
            # 整数放大回全尺寸，避免画面缩小（最近邻，滚动中可接受）
            self._photo = self._photo.zoom(2, 2)

        self.canvas.delete('all')
        ox, oy = self.view_offset()
        self.canvas.create_image(ox, oy, anchor='nw', image=self._photo)

        self.sel_rect = self.sel_label = None
        sel = self.selection()
        if sel:
            sx0, sy0 = self.to_window(sel[0], sel[1])
            sx1, sy1 = self.to_window(sel[2], sel[3])
            self.sel_rect = self.canvas.create_rectangle(sx0, sy0, sx1, sy1, outline='#00ff00', width=1)
            label = f'({sel[0]},{sel[1]})-({sel[2]},{sel[3]}) {sel[2] - sel[0]}x{sel[3] - sel[1]}'
            self.sel_label = self.canvas.create_text(
                sx0, max(sy0 - 6, 12), text=label, fill='#00ff00', anchor='sw', font=('Consolas', 9))

        mx, my = self.mouse
        ox, oy = self.view_offset()
        self.cross_h = self.canvas.create_line(ox, my, ox + self.win_size[0], my, fill='#ff4444')
        self.cross_v = self.canvas.create_line(mx, oy, mx, oy + self.win_size[1], fill='#ff4444')
        self.info_item = self.canvas.create_text(
            ox + 6, oy + 16, text='', fill='#ffff00', anchor='w', font=('Consolas', 9))
        self.update_cursor()
        # 选区选择完成后才显示保存按钮（拖动中不显示）
        if not self.selecting:
            self._update_save_buttons()

    # ---------- 保存 ----------

    def save(self, mode):
        """
        弹出系统文件保存对话框保存选区（类似 PS 导出）。

        Args:
            mode(str): 'button' 黑底保留选区 / 'template' 裁剪选区
        """
        sel = self.selection()
        if not sel:
            self.status('请先在预览图上左键拖选选区')
            return
        title = '保存按钮模板' if mode == 'button' else '保存模板'
        file = filedialog.asksaveasfilename(
            title=title,
            initialdir=self.last_save_dir,
            defaultextension='.png',
            filetypes=[('PNG 图片', '*.png')],
        )
        if not file:
            return  # 用户取消
        folder = os.path.dirname(file)
        name = os.path.splitext(os.path.basename(file))[0].strip().upper()
        if not name:
            return
        # 模板模式自动补 TEMPLATE_ 前缀
        if mode == 'template' and not name.startswith('TEMPLATE_'):
            name = f'TEMPLATE_{name}'
            file = os.path.join(folder, f'{name}.png')

        x0, y0, x1, y1 = sel
        if mode == 'button':
            canvas = np.zeros_like(self.image)
            canvas[y0:y1, x0:x1] = self.image[y0:y1, x0:x1]
            output = canvas
        else:
            output = self.image[y0:y1, x0:x1]

        save_png(file, output)
        self.last_save_dir = folder

        # 按 button_extract 的规则回显提取结果，方便立即验证
        bbox = get_bbox(output)
        color = tuple(int(v) for v in np.rint(get_color(image=output, area=bbox)))
        logger.info(f'Verify: bbox={tuple(int(v) for v in bbox)}, color={color}')
        self.status(f'已保存: {file}')

    # ---------- 按键 / 生命周期 ----------

    def on_key(self, event):
        # 输入框/下拉框聚焦时不触发快捷键
        focused = self.root.focus_get()
        if focused in (self.capture_dir_entry, self.win_combo, self.mon_combo):
            return
        key = event.keysym.lower()
        if key == 'c':
            self.do_capture()
        elif key == 'b':
            self.save('button')
        elif key == 't':
            self.save('template')
        elif key == 'r':
            self.reset_view()
        elif key == 'escape':
            self.quit_app()

    def start_hotkey(self):
        try:
            from pynput import keyboard

            def on_capture():
                self.root.after(0, self.do_capture)

            self.listener = keyboard.GlobalHotKeys({CAPTURE_HOTKEY: on_capture})
            self.listener.start()
            logger.info(f'Global hotkey enabled: {CAPTURE_HOTKEY}')
        except Exception as e:
            self.listener = None
            logger.warning(f'Global hotkey unavailable ({e}), use C key instead')

    def status(self, text):
        self.status_var.set(text)
        logger.info(text)

    def quit_app(self):
        save_config(self.collect_settings())
        if self.listener is not None:
            self.listener.stop()
        self.root.destroy()

    def run(self):
        self.start_hotkey()
        self.render()
        self.root.mainloop()


def main():
    if '--list' in sys.argv:
        for w in list_windows():
            size = f'{w["client_size"][0]}x{w["client_size"][1]}'
            print(f'{w["hwnd"]:>10} | {w["title"]} | {w["class_name"]} | {w["process"]} | {size}')
        print('===== Monitors =====')
        for m in list_monitors():
            print(f'[{m["index"]}] {m["resolution"][0]}x{m["resolution"][1]} work={m["work_area"]}')
        return

    try:
        method = 'mss' if '--mss' in sys.argv else 'printwindow'
        TemplateCaptureApp(capture_method=method).run()
    except Exception as e:
        logger.error(f'Template capture failed: {e}')
        show_error('Template Capture 出错', str(e))


if __name__ == '__main__':
    main()
