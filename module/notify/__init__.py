import sys

from .i18n import get_text


def handle_notify(config, title_key: str, content_key, **kwargs):
    """
    处理通知，支持多语言。

    Args:
        config: NikkeConfig 实例，用于获取 onepush 配置和语言。
        title_key (str): 标题的 i18n key。
        content_key (str or list): 内容的 i18n key。
        **kwargs:
            image_path (str): 图片路径。
            always (bool): 是否在 Windows 上也执行 Linux 推送。
            ... 其他用于格式化字符串的参数。
    """
    # Lazy import onepush
    from module.notify.notify import handle_notify_linux, handle_notify_win
    from module.webui.config import DeployConfig

    lang = DeployConfig().Language

    # 特殊处理招募类型
    if 'recruit_type_key' in kwargs:
        kwargs['recruit_type'] = get_text(f'RecruitType.{kwargs["recruit_type_key"]}', lang)

    format_args = {'config_name': getattr(config, 'config_name', 'nkas'), **kwargs}

    title = get_text(title_key, lang, **format_args)
    if isinstance(content_key, list):
        content = '\n'.join([get_text(key, lang, **format_args) for key in content_key])
    else:
        content = get_text(content_key, lang, **format_args)

    notify_kwargs = {'title': title, 'content': content, 'image_path': kwargs.get('image_path')}

    onepush_config = getattr(config, 'Notification_OnePushConfig', '')
    always = kwargs.get('always', False)

    if sys.platform.startswith('win'):
        handle_notify_win(**notify_kwargs)
        if always:
            handle_notify_linux(onepush_config, **notify_kwargs)
    else:
        handle_notify_linux(onepush_config, **notify_kwargs)
