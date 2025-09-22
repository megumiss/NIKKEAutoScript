class RequestHumanTakeover(Exception):
    # 请求人工接管
    pass


class EmulatorNotRunningError(Exception):
    # 模拟器未运行
    pass


class ScriptError(Exception):
    # This is likely to be a mistake of developers, but sometimes a random issue
    pass


class GameTooManyClickError(Exception):
    pass


class GameStuckError(Exception):
    pass


class GameServerUnderMaintenance(Exception):
    pass


class GameNotRunningError(Exception):
    pass


class GameStart(Exception):
    pass


class GamePageUnknownError(Exception):
    pass


class OperationFailed(Exception):
    pass


class AccountError(Exception):
    pass


class ScreenshotError(Exception):
    pass
