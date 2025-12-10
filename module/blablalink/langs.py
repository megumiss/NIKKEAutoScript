class LangMeta(type):
    _translations = {
        # 兑换
        "Gem_×320": {"en": "Gem ×320", "zh-TW": "珠寶 ×320", "ja": ""},
        "Welcome_Gift_Core_Dust_×30": {"en": "Welcome Gift: Core Dust ×30", "zh-TW": "指揮官見面禮：芯塵 ×30", "ja": ""},
        "Gem_×30": {"en": "Gem ×30", "zh-TW": "珠寶 ×30", "ja": ""},
        "Skill_Manual_I_×5": {"en": "Skill Manual I ×5", "zh-TW": "技能手冊 I ×5", "ja": ""},
        "Ultra_Boost_Module_×5": {"en": "Ultra Boost Module ×5", "zh-TW": "模組高級推進器 ×5", "ja": ""},
        "Code_Manual_Selection_Box_×5": {"en": "Code Manual Selection Box ×5", "zh-TW": "代碼手冊選擇寶箱 ×5", "ja": ""},
        "Gem_×60": {"en": "Gem ×60", "zh-TW": "珠寶 ×60", "ja": ""},
        "Mid-Quality_Mold_×3": {"en": "Mid-Quality Mold ×3", "zh-TW": "中品質鑄模 ×3", "ja": ""},
        "Credit_Case_(1H)_x9": {"en": "Credit Case (1H) x9", "zh-TW": "信用點盒(1H) x9", "ja": ""},
        "Core_Dust_Case_(1H)_×3": {"en": "Core Dust Case (1H) ×3", "zh-TW": "芯塵盒 (1H) ×3", "ja": ""},
        "Gem_×120": {"en": "Gem ×120", "zh-TW": "珠寶 ×120", "ja": ""},
        "Mid-Quality_Mold_×8": {"en": "Mid-Quality Mold ×8", "zh-TW": "中品質鑄模 ×8", "ja": ""},
        "Battle_Data_Set_Case_(1H)_×6": {"en": "Battle Data Set Case (1H) ×6", "zh-TW": "戰鬥數據輯盒 (1H) ×6", "ja": ""},
        "Core_Dust_Case_(1H)_×6": {"en": "Core Dust Case (1H) ×6", "zh-TW": "芯塵盒 (1H) ×6", "ja": ""},
        "Skill_Manual_I_×30": {"en": "Skill Manual I ×30", "zh-TW": "技能手冊 I ×30", "ja": ""},
        "Ultra_Boost_Module_×30": {"en": "Ultra Boost Module ×30", "zh-TW": "模組高級推進器 ×30", "ja": ""},
        "Code_Manual_Selection_Box_×30": {"en": "Code Manual Selection Box ×30", "zh-TW": "代碼手冊選擇寶箱 ×30", "ja": ""},
        # 任务
        "Daily_Sign_in": {"en": "Sign-in", "zh-TW": "每日簽到", "ja": ""},
        "Daily_Play_Game": {"en": "Play", "zh-TW": "遊玩", "ja": ""},
        "Browse_Posts": {"en": "Browse", "zh-TW": "瀏覽", "ja": ""},
        "Like_Posts": {"en": "Like", "zh-TW": "按讚", "ja": ""},
    }
    _current_lang = "zh-TW"

    def __getattr__(cls, name):
        """允许通过属性方式获取翻译"""
        if name in cls._translations:
            values = cls._translations[name]
            return values.get(cls._current_lang, next(iter(values.values())))
        raise AttributeError(f"{cls.__name__} has no attribute '{name}'")

    def use(cls, lang: str):
        """设置当前使用语言"""
        cls._current_lang = lang

    def get(cls, key: str):
        """通过字符串 key 获取翻译"""
        values = cls._translations.get(key)
        if values:
            return values.get(cls._current_lang, next(iter(values.values())))
        raise KeyError(f"No translation for '{key}'")


class BlaLangs(metaclass=LangMeta):
    pass
