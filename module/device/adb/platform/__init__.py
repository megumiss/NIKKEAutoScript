from module.device.adb.env import IS_WINDOWS

if IS_WINDOWS:
    from module.device.adb.platform.platform_windows import PlatformWindows as Platform
else:
    from module.device.adb.platform.platform_base import PlatformBase as Platform
