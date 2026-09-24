import ctypes
import sys

# 串行模式会先导入驱动输入模块；必须在 pyautogui 固定为系统 DPI 之前启用每屏 DPI。
if sys.platform == 'win32':
    try:
        result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if result:
            awareness = ctypes.c_int()
            status = ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
            if status or awareness.value != 2:
                raise OSError(result, 'Unable to enable per-monitor DPI awareness')
    except (AttributeError, OSError) as error:
        from module.logger import logger

        logger.warning(f'Windows DPI initialization failed: {error}')
