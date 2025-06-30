from module.config.yaml_strategy import read, get

LANG = "zh-CN"


def t(s, *args, **kwargs):
    # print(f'{s} {LANG}')
    return _t(s, LANG).format(*args, **kwargs)


def _t(s, lang=None):
    if not lang:
        lang = LANG
    try:
        if not dic_lang.keys():
            reload()
        return get(s, dic_lang)
    except KeyError:
        print(f"Language key ({s}) not found")
        return s


dic_lang: dict[str, dict[str, str]] = {}


def reload():
    global dic_lang
    dic_lang = read('./module/config/i18n/zh-CN.yaml')
