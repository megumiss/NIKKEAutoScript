class LangMeta(type):
    _translations = {
        # 启动游戏/game start/
        "LANUCHER_LAUNCH": {"en-US": "LAUNCH", "zh-CN": "启动", "ja-JP": ""},
        "LANUCHER_UPDATE": {"en-US": "UPDATE", "zh-CN": "更新", "ja-JP": ""},
        "LANUCHER_EMAIL": {"en-US": "Email account", "zh-CN": "电子邮件账号", "ja-JP": ""},
        "LANUCHER_FORGET_PASSWORD": {"en-US": "Forget password", "zh-CN": "忘记密码", "ja-JP": ""},
        "LANUCHER_PASSWORD": {"en-US": "Password", "zh-CN": "密码", "ja-JP": ""},
        "LANUCHER_STAY_LOGGED_IN": {"en-US": "Stay logged in", "zh-CN": "保持登录", "ja-JP": ""},
        "LANUCHER_LOGIN": {"en-US": "LOGIN", "zh-CN": "登录", "ja-JP": ""},
        "LANUCHER_INCORRECT_ACCOUNT_FORMAT": {"en-US": "Incorrect account format", "zh-CN": "账号格式错误", "ja-JP": ""},
        "LANUCHER_ACCOUNT_CONFIGURATION_ERROR": {"en-US": "Account configuration error", "zh-CN": "暂未设置密码", "ja-JP": ""},
        "LANUCHER_INCORRECT_PASSWORD": {"en-US": "Incorrect password", "zh-CN": "密码错误", "ja-JP": ""},
        "LANUCHER_GAME_SETTING": {"en-US": "GAME SETTINGS", "zh-CN": "游戏设置", "ja-JP": ""},
        # 协同作战/coop/
        "COOP_TIMELINE_HOUR": {"en-US": "H", "zh-CN": "小时", "ja-JP": ""},
        "COOP_TIMELINE_LEFT": {"en-US": "Left", "zh-CN": "剩余", "ja-JP": ""},
        "COOP_TIMELINE_TIMEOUT": {"en-US": "Times Up", "zh-CN": "时间到", "ja-JP": ""},
        # 其他
        "CLAIM_ALL": {"en-US": "Claim All", "zh-CN": "全部领取", "ja-JP": ""},
    }
    _current_lang = "zh-CN"

    def __getattr__(cls, name):
        if name in cls._translations:
            values = cls._translations[name]
            return values.get(cls._current_lang, next(iter(values.values())))
        raise AttributeError(f"{cls.__name__} has no attribute '{name}'")

    def use(cls, lang: str):
        """设置当前使用语言"""
        cls._current_lang = lang


class Langs(metaclass=LangMeta):
    pass
