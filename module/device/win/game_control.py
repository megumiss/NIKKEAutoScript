import ctypes
import os
import subprocess
import time
from typing import Literal, Optional, Tuple

import psutil
import pyautogui
import win32con
import win32gui

from module.base.utils import ensure_time
from module.device.win.registry.game_auto_hdr import get_game_auto_hdr, set_game_auto_hdr
from module.device.win.registry.setting import (
    get_game_resolution,
    set_game_resolution,
)
from module.logger import logger


class WinClient:
    def __init__(self, config):
        super().__init__(config)

    def start_game(self) -> bool:
        """启动游戏"""
        if not os.path.exists(self.game_path):
            logger.error(f'游戏路径不存在：{self.game_path}')
            return False

        game_folder = self.game_path.rpartition('\\')[0]
        if not os.system(f'cmd /C start "" /D "{game_folder}" "{self.game_path}"'):
            logger.info(f'游戏启动：{self.game_path}')
            return True
        else:
            logger.error('启动游戏时发生错误')
            try:
                # 为什么有的用户环境变量内没有cmd呢？
                subprocess.Popen(self.game_path)
                logger.info(f'游戏启动：{self.game_path}')
                return True
            except Exception as e:
                logger.error(f'启动游戏时发生错误：{e}')
            return False

    def stop_game(self) -> bool:
        """终止游戏"""
        try:
            # os.system(f'taskkill /f /im {self.process_name}')
            # TODO
            # self.terminate_named_process(self.process_name)
            logger.info(f'游戏终止：{self.process_name}')
            return True
        except Exception as e:
            logger.error(f'终止游戏时发生错误：{e}')
            return False

    @staticmethod
    def sleep(second):
        """
        Args:
            second(int, float, tuple):
        """
        time.sleep(ensure_time(second))

    @staticmethod
    def terminate_named_process(target_process_name, termination_timeout=10):
        """
        根据进程名终止属于当前用户的进程。

        参数:
        - target_process_name (str): 要终止的进程名。
        - termination_timeout (int, optional): 终止进程前等待的超时时间（秒）。

        返回值:
        - bool: 如果成功终止进程则返回True，否则返回False。
        """
        system_username = os.getlogin()  # 获取当前系统用户名
        # 遍历所有运行中的进程
        for process in psutil.process_iter(attrs=['pid', 'name']):
            # 检查当前进程名是否匹配并属于当前用户
            if target_process_name in process.info['name']:
                process_username = process.username().split('\\')[-1]  # 从进程所有者中提取用户名
                if system_username == process_username:
                    proc_to_terminate = psutil.Process(process.info['pid'])
                    proc_to_terminate.terminate()  # 尝试终止进程
                    proc_to_terminate.wait(termination_timeout)  # 等待进程终止

    @staticmethod
    def set_foreground_window_with_retry(hwnd):
        """尝试将窗口设置为前台，失败时先最小化再恢复。"""

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

    def switch_to_game(self) -> bool:
        """将游戏窗口切换到前台"""
        try:
            hwnd = win32gui.FindWindow(self.window_class, self.window_name)
            if hwnd == 0:
                logger.debug('游戏窗口未找到')
                return False
            self.set_foreground_window_with_retry(hwnd)
            logger.info('游戏窗口已切换到前台')
            return True
        except Exception as e:
            logger.error(f'激活游戏窗口时发生错误：{e}')
            return False

    def get_resolution(self) -> Optional[Tuple[int, int]]:
        """检查游戏窗口的分辨率"""
        try:
            hwnd = win32gui.FindWindow(self.window_class, self.window_name)
            if hwnd == 0:
                logger.debug('游戏窗口未找到')
                return None
            _, _, window_width, window_height = win32gui.GetClientRect(hwnd)
            return window_width, window_height
        except IndexError:
            logger.debug('游戏窗口未找到')
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
        self.stop_game()
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

    def change_resolution(self, client_width, client_height):
        """
        设置窗口客户区大小为指定分辨率

        参数:
            client_width: 客户区宽度(像素)
            client_height: 客户区高度(像素)
        """
        try:
            # 查找窗口句柄
            hwnd = win32gui.FindWindow(self.window_class, self.window_name)
            if hwnd == 0:
                logger.error('游戏窗口未找到')
                raise Exception('游戏窗口未找到')

            # 获取窗口矩形和客户区矩形
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

            # 设置窗口大小
            result = win32gui.SetWindowPos(
                hwnd, 0, 0, 0, window_width, window_height, win32con.SWP_NOMOVE | win32con.SWP_NOZORDER
            )

            if result == 0:
                logger.error('设置窗口大小失败')
                raise Exception('设置窗口大小失败')

            # 验证设置是否成功
            new_rect = win32gui.GetClientRect(hwnd)
            logger.debug(f'设置后的客户区大小: {new_rect[2]}x{new_rect[3]}')

            if new_rect[2] != client_width and new_rect[3] != client_height:
                logger.warning(
                    f'设置分辨率不完全匹配: 期望 {client_width}x{client_height}, 实际 {new_rect[2]}x{new_rect[3]}'
                )
            else:
                logger.info(f'成功设置窗口客户区分辨率为: {client_width}x{client_height}')

        except Exception as e:
            logger.error(f'设置窗口分辨率时发生错误: {e}')
            logger.error(f'目标分辨率: {client_width}x{client_height}')
            raise Exception(f'无法设置窗口分辨率: {e}')

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
                    f'修改游戏分辨率: {self.game_resolution[0]}x{self.game_resolution[1]} ({"全屏" if self.game_resolution[2] else "窗口"}) --> {width}x{height} ({"全屏" if is_fullscreen else "窗口"})'
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
                    f'恢复游戏分辨率: {self.game_resolution[0]}x{self.game_resolution[1]} ({"全屏" if self.game_resolution[2] else "窗口"})'
                )
        except Exception as e:
            logger.error(f'写入注册表值时发生错误: {e}')

    def change_auto_hdr(self, status: Literal['enable', 'disable', 'unset'] = 'unset'):
        """通过注册表修改游戏自动 HDR 设置"""
        status_map = {'enable': '启用', 'disable': '禁用', 'unset': '未设置'}
        try:
            self.game_auto_hdr = get_game_auto_hdr(self.game_path)
            if self.game_auto_hdr == status:
                self.game_auto_hdr = None
                return
            set_game_auto_hdr(self.game_path, status)
            logger.debug(f'修改游戏自动 HDR: {status_map.get(self.game_auto_hdr)} --> {status_map.get(status)}')
        except Exception as e:
            logger.debug(f'修改游戏自动 HDR 设置时发生错误：{e}')

    def restore_auto_hdr(self):
        """通过注册表恢复游戏自动 HDR 设置"""
        status_map = {'enable': '启用', 'disable': '禁用', 'unset': '未设置'}
        try:
            if self.game_auto_hdr:
                set_game_auto_hdr(self.game_path, self.game_auto_hdr)
            logger.debug(f'恢复游戏自动 HDR: {status_map.get(self.game_auto_hdr)}')
        except Exception as e:
            logger.debug(f'恢复游戏自动 HDR 设置时发生错误：{e}')

    def check_resolution(self, target_width: int, target_height: int) -> None:
        """
        检查游戏窗口的分辨率是否匹配目标分辨率。

        如果游戏窗口的分辨率与目标分辨率不匹配，则记录错误并抛出异常。
        如果桌面分辨率小于目标分辨率，也会记录错误建议。

        参数:
            target_width (int): 目标分辨率的宽度。
            target_height (int): 目标分辨率的高度。
        """
        self.screen_resolution = pyautogui.size()
        screen_width, screen_height = self.screen_resolution
        if screen_width < target_width or screen_height < target_height:
            logger.error(f'桌面分辨率: {screen_width}x{screen_height}，目标分辨率: {target_width}x{target_height}')
            logger.error(
                f'显示器横向分辨率必须大于 {target_width}，竖向分辨率必须大于 {target_height}；请尝试竖屏使用，或者更换更大的显示器/使用 HDMI/VGA 显卡欺骗器'
            )
            raise Exception('桌面分辨率过低')
        else:
            logger.debug(f'桌面分辨率: {screen_width}x{screen_height}')

    def check_resolution_ratio(self, target_width: int, target_height: int) -> None:
        """
        检查游戏窗口的分辨率和比例是否符合目标设置。

        如果游戏窗口的分辨率小于目标分辨率或比例不正确，则记录错误并抛出异常。
        如果桌面分辨率不符合最小推荐值，也会记录错误建议。

        参数:
            target_width (int): 目标分辨率的宽度。
            target_height (int): 目标分辨率的高度。
        """
        resolution = self.get_resolution()
        if not resolution:
            raise Exception('游戏分辨率获取失败')
        window_width, window_height = resolution

        if window_width != target_width and window_height != target_height:
            logger.error(
                f'游戏分辨率: {window_width}x{window_height} ≠ {target_width}x{target_height}，分辨率错误，请重试'
            )
            raise Exception('游戏分辨率错误')
        else:
            logger.debug(f'游戏分辨率: {window_width}x{window_height}')
