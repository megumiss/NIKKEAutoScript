import ctypes

from module.exception import ScreenResolutionNotEnough

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    pass
import os
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Literal, Optional, Tuple

import psutil
import win32api
import win32con
import win32gui
from desktopmagic.screengrab_win32 import getDisplayRects
from numpy import ndarray

from module.base.utils import ensure_time
from module.device.win.registry.game_auto_hdr import get_game_auto_hdr, set_game_auto_hdr
from module.device.win.registry.setting import get_game_resolution, set_game_resolution
from module.logger import logger


@dataclass
class Window:
    """统一的窗口对象"""

    name: str
    title: str
    class_name: str
    process: str
    path: str
    hwnd: int = field(default=0)
    resolution: tuple = field(default=None)
    offset: tuple = field(default=(0, 0))
    image: ndarray = field(default=None)
    screenshot_scale_factor: float = field(default=1.0)


class WinClient:
    def __init__(self, config):
        super().__init__(config)

    def start_program(self) -> bool:
        """启动程序"""
        logger.info(f'启动程序：[{self.current_window.name}]:{self.current_window.path}')
        path = self.current_window.path
        if not os.path.exists(path):
            logger.error('路径不存在')
            return False

        folder = path.rpartition('\\')[0]
        if not os.system(f'cmd /C start "" /D "{folder}" "{path}"'):
            logger.info('程序启动成功')
            return True
        else:
            logger.error('程序启动时发生错误')
            try:
                subprocess.Popen(path)
                logger.info('程序启动成功')
                return True
            except Exception as e:
                logger.error(f'程序启动时发生错误：{e}')
            return False

    def stop_program(self) -> bool:
        """终止程序"""
        logger.info(f'终止程序：[{self.current_window.name}]:{self.current_window.process}')
        process = self.current_window.process
        try:
            self.terminate_named_process(process)
            logger.info('程序终止成功')
            return True
        except Exception as e:
            logger.error(f'终止时发生错误：{e}')
            return False

    def check_program(self) -> bool:
        """检查程序是否启动"""
        logger.info(f'检查程序：[{self.current_window.name}]:{self.current_window.process}')
        process = self.current_window.process
        try:
            if self.is_process_running(process):
                logger.info('程序启动成功')
                return True
            else:
                False
        except Exception as e:
            logger.error(f'检查程序发生错误：{e}')
            return False

    @staticmethod
    def terminate_named_process(target_process, termination_timeout=10):
        """
        根据进程名终止属于当前用户的进程。

        参数:
        - target_process (str): 要终止的进程名。
        - termination_timeout (int, optional): 终止进程前等待的超时时间（秒）。

        返回值:
        - bool: 如果成功终止进程则返回True, 否则返回False。
        """
        system_username = os.getlogin()  # 获取当前系统用户名
        # 遍历所有运行中的进程
        for process in psutil.process_iter(attrs=['pid', 'name']):
            # 检查当前进程名是否匹配并属于当前用户
            if target_process in process.info['name']:
                process_username = process.username().split('\\')[-1]  # 从进程所有者中提取用户名
                if system_username == process_username:
                    proc_to_terminate = psutil.Process(process.info['pid'])
                    proc_to_terminate.terminate()
                    proc_to_terminate.wait(termination_timeout)

    @staticmethod
    def is_process_running(target_process: str) -> bool:
        """
        检查指定进程名是否正在运行（仅限当前用户）。

        参数:
        - target_process (str): 要检查的进程名。

        返回值:
        - bool: 如果进程存在并属于当前用户则返回 True，否则返回 False。
        """
        try:
            system_username = os.getlogin()  # 当前系统用户名
        except Exception:
            # 有时 os.getlogin() 在服务或计划任务中会失败，用这种方式兜底
            import getpass

            system_username = getpass.getuser()

        for process in psutil.process_iter(attrs=['pid', 'name', 'username']):
            try:
                if target_process.lower() in (process.info['name'] or '').lower():
                    process_username = process.info['username'].split('\\')[-1]
                    if system_username == process_username:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    @staticmethod
    def screen_rotate(screen_n=0, orientation=0):
        """
        设置屏幕方向
        orientation: 0=横屏, 1=竖屏(90), 2=横屏翻转, 3=竖屏(270)
        """
        device = win32api.EnumDisplayDevices(None, screen_n)
        dm = win32api.EnumDisplaySettings(device.DeviceName, win32con.ENUM_CURRENT_SETTINGS)

        # 如果当前方向和目标方向不一样
        if dm.DisplayOrientation != orientation:
            # 如果当前是横屏<->竖屏切换，需要交换分辨率宽高
            if (dm.DisplayOrientation + orientation) % 2 == 1:
                dm.PelsWidth, dm.PelsHeight = dm.PelsHeight, dm.PelsWidth

            dm.DisplayOrientation = orientation
            win32api.ChangeDisplaySettingsEx(device.DeviceName, dm)
            logger.info(f'设置屏幕方向：{orientation}')

    @staticmethod
    def set_foreground_window_with_retry(hwnd):
        """尝试将窗口设置为前台，失败时先最小化再恢复"""

        def toggle_window_state(hwnd, minimize=False):
            """最小化或恢复窗口。"""
            SW_MINIMIZE = 6
            SW_RESTORE = 9
            state = SW_MINIMIZE if minimize else SW_RESTORE
            ctypes.windll.user32.ShowWindow(hwnd, state)

        toggle_window_state(hwnd, minimize=False)
        if ctypes.windll.user32.SetForegroundWindow(hwnd) == 0:
            toggle_window_state(hwnd, minimize=True)
            toggle_window_state(hwnd, minimize=False)
            if ctypes.windll.user32.SetForegroundWindow(hwnd) == 0:
                raise Exception('Failed to set window foreground')

    def switch_to_program(self) -> bool:
        """将程序窗口切换到前台"""
        logger.info(f'将窗口切换到前台：[{self.current_window.name}]:{self.current_window.title}')
        try:
            hwnd = win32gui.FindWindow(self.current_window.class_name, self.current_window.title)
            if hwnd == 0:
                logger.warning('窗口未找到')
                return False
            self.set_foreground_window_with_retry(hwnd)
            logger.info('窗口已切换到前台')
            return True
        except Exception as e:
            logger.error(f'激活窗口时发生错误：{e}')
            return False

    def get_resolution(self) -> Optional[Tuple[int, int]]:
        """获取程序窗口的分辨率"""
        logger.info(f'获取窗口分辨率：[{self.current_window.name}]:{self.current_window.title}')
        try:
            hwnd = win32gui.FindWindow(self.current_window.class_name, self.current_window.title)
            if hwnd == 0:
                logger.warning('窗口未找到')
                return None
            _, _, window_width, window_height = win32gui.GetClientRect(hwnd)
            self.current_window.resolution = (window_width, window_height)
            return window_width, window_height
        except IndexError:
            logger.warning('窗口未找到')
            return None

    def shutdown(
        self,
        action: Literal['Exit', 'Loop', 'Shutdown', 'Sleep', 'Hibernate', 'Restart', 'Logoff', 'RunScript'],
        delay: int = 60,
    ) -> bool:
        """
        终止游戏并在指定的延迟后执行系统操作：关机、睡眠、休眠、重启、注销。

        参数:
            action: 要执行的系统操作。
            delay: 延迟时间，单位为秒，默认为60秒。

        返回:
            操作成功执行返回True，否则返回False。
        """
        self.stop_program(self.current_window)
        if action not in ['Shutdown', 'Sleep', 'Hibernate', 'Restart', 'Logoff', 'RunScript']:
            return True

        logger.warning(f'将在{delay}秒后开始执行系统操作：{action}')
        time.sleep(delay)  # 暂停指定的秒数

        try:
            if action == 'Shutdown':
                os.system('shutdown /s /t 0')
            elif action == 'Sleep':
                # 必须先关闭休眠，否则下面的指令不会进入睡眠，而是优先休眠
                os.system('powercfg -h off')
                os.system('rundll32.exe powrprof.dll,SetSuspendState 0,1,0')
                os.system('powercfg -h on')
            elif action == 'Hibernate':
                os.system('shutdown /h')
            elif action == 'Restart':
                os.system('shutdown /r')
            elif action == 'Logoff':
                os.system('shutdown /l')
            elif action == 'RunScript':
                self.run_script()
            logger.info(f'执行系统操作：{action}')
            return True
        except Exception as e:
            logger.error(f'执行系统操作时发生错误：{action}, 错误：{e}')
            return False

    def run_script(self):
        """运行指定的程序或脚本（支持.exe、.ps1和.bat）"""
        if not self.script_path or not isinstance(self.script_path, str) or not os.path.exists(self.script_path):
            logger.warning(f'指定的路径无效或不存在：{self.script_path}')
            return False

        try:
            # 获取脚本所在目录
            script_dir = os.path.dirname(os.path.abspath(self.script_path))
            # 保存当前工作目录
            original_cwd = os.getcwd()

            try:
                # 切换到脚本所在目录
                os.chdir(script_dir)

                file_ext = os.path.splitext(self.script_path)[1].lower()
                if file_ext == '.ps1':
                    # PowerShell脚本
                    subprocess.Popen(
                        ['powershell', '-ExecutionPolicy', 'Bypass', '-File', self.script_path],
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                    logger.info(f'已启动PowerShell脚本：{self.script_path}')
                elif file_ext == '.bat':
                    # Batch脚本
                    subprocess.Popen([self.script_path], shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    logger.info(f'已启动Batch脚本：{self.script_path}')
                elif file_ext == '.exe':
                    # 可执行文件
                    subprocess.Popen([self.script_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    logger.info(f'已启动可执行文件：{self.script_path}')
                else:
                    logger.warning(f'不支持的文件类型：{file_ext}')
                    return False
                return True
            finally:
                # 恢复原始工作目录
                os.chdir(original_cwd)
        except Exception as e:
            logger.error(f'启动脚本时发生错误：{str(e)}')
            return False

    def calculate_window_position(self, screen_n, window_width, window_height, position='center'):
        """
        根据位置模式计算窗口放置的坐标（支持多显示器）

        参数:
            window_width: 窗口宽度（含边框）
            window_height: 窗口高度（含边框）
            position: 位置模式，支持 center/left/right/topleft/topright

        返回:
            (x, y): 窗口左上角坐标 (coordenadas virtuais)
        """
        all_screens = getDisplayRects()

        # Garantir que o número do monitor seja válido
        if not (0 <= screen_n < len(all_screens)):
            logger.warning(
                f'Número do monitor {screen_n} inválido. Total de monitores: {len(all_screens)}. Usando monitor 0.'
            )
            screen_n = 0

        target_screen = all_screens[screen_n]
        screen_left, screen_top, screen_right, screen_bottom = target_screen

        screen_width = screen_right - screen_left
        screen_height = screen_bottom - screen_top

        if position == 'center':
            x_rel = (screen_width - window_width) // 2
            y_rel = (screen_height - window_height) // 2
        elif position == 'left':
            x_rel = 0
            y_rel = (screen_height - window_height) // 2
        elif position == 'right':
            x_rel = screen_width - window_width
            y_rel = (screen_height - window_height) // 2
        elif position == 'topleft':
            x_rel, y_rel = 0, 0
        elif position == 'topright':
            x_rel = screen_width - window_width
            y_rel = 0
        else:
            # Padrão é 'center'
            x_rel = (screen_width - window_width) // 2
            y_rel = (screen_height - window_height) // 2

        # Calcular coordenadas absolutas (virtuais)
        x = screen_left + x_rel
        y = screen_top + y_rel

        # Prevenir que a janela saia dos limites do monitor alvo
        x = max(screen_left, min(x, screen_right - window_width))
        y = max(screen_top, min(y, screen_bottom - window_height))

        return x, y

    def change_resolution(self, screen_n, client_width, client_height, position='center'):
        """
        设置窗口客户区大小为指定分辨率，并调整位置
        参数:
            client_width: 客户区宽度(像素)
            client_height: 客户区高度(像素)
            position: 窗口位置（center/left/right/topleft/topright）
        """
        logger.info(
            f'设置窗口分辨率：[{self.current_window.name}]:{self.current_window.title} '
            f'{client_width}x{client_height}, 位置={position}'
        )
        try:
            # 查找窗口句柄
            hwnd = win32gui.FindWindow(self.current_window.class_name, self.current_window.title)
            if hwnd == 0:
                logger.error('窗口未找到')
                raise Exception('窗口未找到')
            rect = win32gui.GetClientRect(hwnd)
            window_rect = win32gui.GetWindowRect(hwnd)
            logger.debug(f'原始窗口矩形: {window_rect}, 客户区矩形: {rect}')
            # 计算边框宽度和高度
            border_width = (window_rect[2] - window_rect[0] - rect[2]) // 2
            border_height = (window_rect[3] - window_rect[1] - rect[3]) // 2

            logger.debug(f'计算得到的边框宽度: {border_width}, 边框高度: {border_height}')

            # 计算需要的窗口大小（包括边框）
            window_width = client_width + 2 * border_width
            window_height = client_height + 2 * border_height

            logger.debug(f'需要设置的窗口大小: {window_width}x{window_height}')

            # 计算位置
            x, y = self.calculate_window_position(screen_n, window_width, window_height, position)
            result = win32gui.SetWindowPos(hwnd, 0, x, y, window_width, window_height, win32con.SWP_NOZORDER)
            if result == 0:
                logger.error('设置窗口大小失败')
                raise Exception('设置窗口大小失败')

            # 验证设置是否成功
            new_rect = win32gui.GetClientRect(hwnd)
            logger.debug(f'设置后的客户区大小: {new_rect[2]}x{new_rect[3]}')

            if new_rect[2] != client_width or new_rect[3] != client_height:
                logger.warning(
                    f'设置分辨率不完全匹配: 期望 {client_width}x{client_height}, 实际 {new_rect[2]}x{new_rect[3]}'
                )
            else:
                logger.info(f'成功设置客户区分辨率为: {client_width}x{client_height}')
        except Exception as e:
            logger.error(f'设置分辨率时发生错误: {e}')
            raise Exception(f'无法设置分辨率: {e}')

    def change_resolution_compat(self, screen_n, client_width, client_height, position='center'):
        """
        设置窗口客户区大小为指定分辨率，并调整位置

        参数:
            screen_n: 屏幕编号
            client_width: 客户区宽度(像素)
            client_height: 客户区高度(像素)
            position: 窗口位置（center/left/right/topleft/topright）
        """
        logger.info(
            f'设置窗口分辨率[兼容模式]：[{self.current_window.name}]:{self.current_window.title} '
            f'{client_width}x{client_height}, 位置={position}'
        )
        try:
            # 查找窗口句柄
            hwnd = win32gui.FindWindow(self.current_window.class_name, self.current_window.title)
            if hwnd == 0:
                logger.error('窗口未找到')
                raise Exception('窗口未找到')

            # 获取窗口的 DPI（Win10+ 支持）
            try:
                GetDpiForWindow = ctypes.windll.user32.GetDpiForWindow
                dpi = GetDpiForWindow(hwnd)
            except Exception:
                dpi = 96  # 不支持时默认 96
            logger.info(f'窗口 DPI: {dpi / 96 * 100}%')

            # 准备调用 AdjustWindowRectExForDpi
            AdjustWindowRectExForDpi = ctypes.windll.user32.AdjustWindowRectExForDpi
            AdjustWindowRectExForDpi.argtypes = [
                ctypes.POINTER(wintypes.RECT),
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
                wintypes.UINT,
            ]
            AdjustWindowRectExForDpi.restype = wintypes.BOOL

            # 先构造一个客户区矩形，考虑 DPI 缩放
            rect = wintypes.RECT(0, 0, int(client_width), int(client_height))

            # 获取当前窗口的 style 和 ex_style
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)

            # 调整矩形，使之包含边框和标题栏
            success = AdjustWindowRectExForDpi(ctypes.byref(rect), style, False, ex_style, dpi)
            if not success:
                raise Exception('AdjustWindowRectExForDpi 调用失败')

            window_width = rect.right - rect.left
            window_height = rect.bottom - rect.top
            logger.debug(f'需要设置的窗口矩形（含边框）: {window_width}x{window_height}')

            # 计算窗口位置
            x, y = self.calculate_window_position(screen_n, window_width, window_height, position)
            result = win32gui.SetWindowPos(hwnd, 0, x, y, window_width, window_height, win32con.SWP_NOZORDER)
            if result == 0:
                logger.error('设置窗口大小失败')
                raise Exception('设置窗口大小失败')

            # 验证客户区大小
            new_rect = win32gui.GetClientRect(hwnd)
            logger.debug(f'设置后的客户区大小: {new_rect[2]}x{new_rect[3]}')

            if new_rect[2] != client_width or new_rect[3] != client_height:
                logger.warning(
                    f'设置分辨率不完全匹配: 期望 {client_width}x{client_height}, 实际 {new_rect[2]}x{new_rect[3]}'
                )
            else:
                logger.info(f'成功设置客户区分辨率为: {client_width}x{client_height}')

        except Exception as e:
            logger.error(f'设置分辨率时发生错误: {e}')
            raise Exception(f'无法设置分辨率: {e}')

    def ensure_resolution(
        self, screen_n, client_width, client_height, position, retries=5, interval=1.0, stable_time=5.0
    ):
        """确保窗口分辨率被成功设置，并保持一段时间"""
        logger.info(f'持续设置窗口分辨率：[{self.current_window.name}]:{self.current_window.title}')
        compat = getattr(self.config, f'PCClient_{self.current_window.name}ResolutionCompat', False)
        for i in range(retries):
            if compat:
                self.change_resolution_compat(screen_n, client_width, client_height, position)
            else:
                self.change_resolution(screen_n, client_width, client_height, position)
            time.sleep(interval)
            hwnd = win32gui.FindWindow(self.current_window.class_name, self.current_window.title)
            if hwnd:
                rect = win32gui.GetClientRect(hwnd)
                if rect[2] == client_width and rect[3] == client_height:
                    # 需要维持 stable_time
                    start = time.time()
                    while time.time() - start < stable_time:
                        time.sleep(0.5)  # 检查间隔
                        rect = win32gui.GetClientRect(hwnd)
                        if rect[2] != client_width or rect[3] != client_height:
                            logger.warning('分辨率在稳定检测过程中变化，重试中...')
                            break
                    else:
                        logger.info(f'分辨率成功保持为 {client_width}x{client_height} 超过 {stable_time} 秒')
                        return True
                    # 如果 break 了就继续外层 for 循环
                else:
                    logger.warning('分辨率未成功设置，重试中...')
        logger.error(f'分辨率无法稳定设置为 {client_width}x{client_height}')
        return False

    def change_reg_resolution(self, width: int, height: int):
        """通过注册表修改游戏分辨率"""
        try:
            self.game_resolution = get_game_resolution()
            if self.game_resolution:
                screen_width, screen_height = self.screen_resolution
                is_fullscreen = False if screen_width > width or screen_height > height else True
                if (
                    self.game_resolution[0] == width
                    and self.game_resolution[1] == height
                    and self.game_resolution[2] == is_fullscreen
                ):
                    self.game_resolution = None
                    return
                set_game_resolution(width, height, is_fullscreen)
                logger.debug(
                    f'修改游戏分辨率: {self.game_resolution[0]}x{self.game_resolution[1]} '
                    f'({"全屏" if self.game_resolution[2] else "窗口"}) --> '
                    f'{width}x{height} ({"全屏" if is_fullscreen else "窗口"})'
                )
        except FileNotFoundError:
            logger.debug('指定的注册表项未找到')
        except Exception as e:
            logger.error(f'读取注册表值时发生错误: {e}')

    def restore_resolution(self):
        """通过注册表恢复游戏分辨率"""
        try:
            if self.game_resolution:
                set_game_resolution(self.game_resolution[0], self.game_resolution[1], self.game_resolution[2])
                logger.debug(
                    f'恢复游戏分辨率: {self.game_resolution[0]}x{self.game_resolution[1]} '
                    f'({"全屏" if self.game_resolution[2] else "窗口"})'
                )
        except Exception as e:
            logger.error(f'写入注册表值时发生错误: {e}')

    def change_auto_hdr(self, status: Literal['enable', 'disable', 'unset'] = 'unset'):
        """通过注册表修改游戏自动 HDR 设置"""
        status_map = {'enable': '启用', 'disable': '禁用', 'unset': '未设置'}
        try:
            game_auto_hdr = get_game_auto_hdr(self.current_window.path)
            if game_auto_hdr == status:
                logger.info(f'游戏自动 HDR 状态: {status_map.get(game_auto_hdr)}')
                return
            set_game_auto_hdr(self.current_window.path, status)
            logger.info(f'修改游戏自动 HDR: {status_map.get(game_auto_hdr)} --> {status_map.get(status)}')
        except Exception as e:
            logger.warning(f'修改游戏自动 HDR 设置时发生错误：{e}')

    def restore_auto_hdr(self):
        """通过注册表恢复游戏自动 HDR 设置"""
        status_map = {'enable': '启用', 'disable': '禁用', 'unset': '未设置'}
        try:
            set_game_auto_hdr(self.current_window.path, 'unset')
            logger.info(f'恢复游戏自动 HDR: {status_map.get("unset")}')
        except Exception as e:
            logger.warning(f'恢复游戏自动 HDR 设置时发生错误：{e}')

    def get_physical_resolutions(self):
        """返回所有屏幕的分辨率"""
        monitors = win32api.EnumDisplayMonitors()
        resolutions = []
        for i, (hMonitor, hDC, (left, top, right, bottom)) in enumerate(monitors):
            width = right - left
            height = bottom - top
            resolutions.append((width, height))
        return resolutions

    def check_screen_resolution(self, screen_n, target_width: int, target_height: int) -> None:
        """
        检查指定屏幕的分辨率是否符合要求。
        如果小于目标分辨率，则记录错误并抛出异常。
        """
        logger.info(f'检查屏幕 {screen_n} 分辨率')

        try:
            monitors = self.get_physical_resolutions()
            if not monitors:
                raise Exception('未检测到任何显示器')

            if screen_n >= len(monitors):
                raise Exception(f'屏幕编号 {screen_n} 超出范围（共 {len(monitors)} 个屏幕）')

            screen_width, screen_height = monitors[screen_n]
            logger.info(f'屏幕 {screen_n} 分辨率: {screen_width}x{screen_height}')

            if screen_width < target_width or screen_height < target_height:
                logger.error(
                    f'屏幕 {screen_n} 分辨率: {screen_width}x{screen_height}，目标: {target_width}x{target_height}'
                )
                logger.error(
                    f'屏幕 {screen_n} 分辨率不足，请开启旋转屏幕/将屏幕设置为纵向/更换高分辨率显示器，'
                    f'或使用显卡欺骗器/UU超级屏/虚拟扩展屏幕。'
                )
                raise ScreenResolutionNotEnough
            else:
                logger.debug(f'屏幕 {screen_n} 分辨率符合要求')
        except Exception as e:
            logger.error(f'检查分辨率时发生错误: {e}')
            raise

    def check_resolution(self, target_width: int, target_height: int) -> None:
        """
        检查游戏窗口的分辨率是否符合目标设置。

        如果游戏窗口的分辨率小于目标分辨率，则记录错误并抛出异常。

        参数:
            target_width (int): 目标分辨率的宽度。
            target_height (int): 目标分辨率的高度。
        """
        logger.info(f'检查窗口分辨率：[{self.current_window.name}]:{self.current_window.title}')

        resolution = self.get_resolution()
        if not resolution:
            raise Exception('窗口分辨率获取失败')
        window_width, window_height = resolution

        if window_width != target_width or window_height != target_height:
            logger.error(f'窗口分辨率: {window_width}x{window_height} ≠ {target_width}x{target_height}，分辨率错误')
            raise Exception('窗口分辨率错误')
        else:
            logger.info(f'窗口分辨率: {window_width}x{window_height}')

    @staticmethod
    def sleep(second):
        """
        Args:
            second(int, float, tuple):
        """
        time.sleep(ensure_time(second))
