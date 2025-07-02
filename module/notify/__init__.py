import sys


def handle_notify(*args, **kwargs):
    # Lazy import onepush
    from module.notify.notify import handle_notify_linux, handle_notify_win

    if sys.platform.startswith("win"):
        return handle_notify_win(**kwargs)
    else:
        return handle_notify_linux(*args, **kwargs)
