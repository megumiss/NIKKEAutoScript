"""Notification settings endpoints: channel metadata, form writes and test push."""

import asyncio
import sys

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.deep import deep_get
from module.logger import logger
from module.notify import providers as notify_providers
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.api.service_config import ConfigService
from module.webui.setting import State

ONEPUSH_KEY = 'NKAS.Notification.OnePushConfig'


def _describe(name: str) -> dict:
    """渠道清单 + 当前 OnePushConfig 的解析结果。"""
    from module.webui.config import DeployConfig

    config = State.config_updater.read_file(name)
    text = deep_get(config, ONEPUSH_KEY, '') or ''
    return notify_providers.describe_plan(text, DeployConfig().Language)


async def providers(request: Request):
    """供任务页「通知渠道」组件渲染的渠道与字段定义。"""
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    return JSONResponse(await asyncio.to_thread(_describe, name))


async def save_config(request: Request):
    """把组件表单写回 OnePushConfig；YAML 文本仍是对外唯一的存储形式。"""
    name = request.path_params['name']
    try:
        validate_instance(name)
        data = await request.json()
        provider = str(data.get('provider') or '')
        params = data.get('params') or {}
        if not isinstance(params, dict):
            raise ValueError
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    except (KeyError, ValueError, TypeError):
        return JSONResponse({'status': 'error', 'message': 'Expected provider and params JSON fields.'}, status_code=400)
    if provider and provider not in notify_providers.PROVIDER_IDS:
        return JSONResponse({'status': 'error', 'message': f'Unknown provider: {provider}'}, status_code=400)

    config = {'provider': provider or None}
    for key, value in params.items():
        coerced = notify_providers.coerce(key, value, provider)
        # 空值不落盘：写空串会覆盖 onepush 的默认值，也会让 YAML 变脏。
        if coerced is not None:
            config[key] = coerced
    text = notify_providers.write_config(config)

    result = ConfigService().patch(name, ONEPUSH_KEY, text)
    payload = result.dict()
    payload['status'] = 'success' if result.ok else 'error'
    if result.ok:
        payload['value'] = text
        payload['config'] = config
    return JSONResponse(payload, status_code=200 if result.ok else 422 if result.invalid else 500)


def _send_test(name: str) -> dict:
    from module.notify.i18n import get_text
    from module.notify.notify import handle_notify_linux, handle_notify_win
    from module.webui.config import DeployConfig

    config = State.config_updater.read_file(name)
    onepush_config = deep_get(config, ONEPUSH_KEY, '') or ''
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
