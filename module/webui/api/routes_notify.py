"""Send a test notification to verify the notification settings."""

import asyncio
import sys

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.deep import deep_get
from module.logger import logger
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.setting import State


def _send_test(name: str) -> dict:
    from module.notify.i18n import get_text
    from module.notify.notify import handle_notify_linux, handle_notify_win
    from module.webui.config import DeployConfig

    config = State.config_updater.read_file(name)
    onepush_config = deep_get(config, 'NKAS.Notification.OnePushConfig', '') or ''
    lang = DeployConfig().Language
    kwargs = {
        'title': get_text('Test.title', lang, config_name=name),
        'content': get_text('Test.content', lang, config_name=name),
    }
    # The test button is not gated by the notification switches: Windows
    # always shows a toast, and OnePush is actually pushed whenever
    # OnePushConfig is filled in, regardless of the WinOnePush toggle.
    result = {}
    if sys.platform.startswith('win'):
        try:
            handle_notify_win(**kwargs)
            result['windows'] = True
        except Exception as e:
            logger.exception(e)
            result['windows'] = False
        if onepush_config.strip():
            result['onepush'] = handle_notify_linux(onepush_config, **kwargs)
    else:
        result['onepush'] = handle_notify_linux(onepush_config, **kwargs)
    return result


async def test_notify(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    result = await asyncio.to_thread(_send_test, name)
    return JSONResponse({'ok': all(result.values()), **result})
