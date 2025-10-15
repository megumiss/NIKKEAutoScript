import sys


def handle_notify(*args, **kwargs):
    # Lazy import onepush
    from module.notify.notify import handle_notify_linux, handle_notify_win

    if sys.platform.startswith('win'):
        handle_notify_win(**kwargs)
        if kwargs.get('always'):
            handle_notify_linux(*args, **kwargs)
    else:
        handle_notify_linux(*args, **kwargs)
