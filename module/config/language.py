language = 'zh-cn'

VALID_LANGUAGE = ['zh-cn']

def set_language(l: str):
    global language
    language = to_language(l)

    from module.base.resource import release_resources
    release_resources()

def to_language(l: str) -> str:
    if l:
        return l
    else:
        return 'zh-cn'