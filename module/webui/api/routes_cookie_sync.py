"""Browser-extension Cookie sync request endpoints."""

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.webui.api.cookie_sync import create, finish, get, pending, parse_cookie
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.api.service_config import ConfigService


REQUIRED_COOKIES = {
    'game_openid', 'game_channelid', 'game_token', 'game_gameid',
    'game_login_game', 'game_adult_status', 'game_uid', 'OptanonConsent',
}


def _cors_headers(request: Request):
    origin = request.headers.get('origin', '')
    if origin.startswith(('chrome-extension://', 'moz-extension://')):
        return {
            'Access-Control-Allow-Origin': origin,
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Private-Network': 'true',
            'Vary': 'Origin',
        }
    return {}


def _json(request: Request, payload, status_code=200):
    return JSONResponse(payload, status_code=status_code, headers=_cors_headers(request))


async def options(request: Request):
    return _json(request, {})


async def request_sync(request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise TypeError
        instance = str(data.get('instance', 'nkas')).strip()
        cookie = str(data.get('cookie', '')).strip()
        validate_instance(instance)
    except InstanceNotFound as exc:
        return _json(request, {'status': 'error', 'message': str(exc)}, 404)
    except (TypeError, ValueError):
        return _json(request, {'status': 'error', 'message': 'Expected instance and cookie JSON fields.'}, 400)

    values = parse_cookie(cookie)
    missing = sorted(REQUIRED_COOKIES - values.keys())
    if missing:
        message = 'Missing required Cookie fields: ' + ', '.join(missing)
        return _json(request, {'status': 'error', 'message': message}, 422)
    if len(cookie) > 16384:
        return _json(request, {'status': 'error', 'message': 'Cookie value is too large.'}, 422)

    sync_request = create(instance, cookie)
    return _json(request, {
        'status': 'pending',
        'request_id': sync_request.request_id,
        'status_url': f'/api/cookie-sync/status/{sync_request.request_id}',
        'message': 'Waiting for NKAS confirmation.',
    }, 202)


async def status(request: Request):
    request_id = request.path_params['request_id']
    sync_request = get(request_id)
    if sync_request is None:
        return _json(request, {'status': 'error', 'message': 'Sync request expired or does not exist.'}, 404)
    return _json(request, sync_request.summary())


async def pending_requests(request: Request):
    return JSONResponse({'status': 'success', 'pending': [item.summary() for item in pending()]})


async def confirm(request: Request):
    request_id = request.path_params['request_id']
    sync_request = get(request_id)
    if sync_request is None:
        return JSONResponse({'status': 'error', 'message': 'Sync request expired or does not exist.'}, status_code=404)
    try:
        result = ConfigService().patch(sync_request.instance, 'BlaAuth.BlaAuth.Cookie', sync_request.cookie)
    except Exception as exc:
        finish(request_id, 'error', str(exc))
        return JSONResponse({'status': 'error', 'message': 'Failed to save Cookie: ' + str(exc)}, status_code=500)
    if not result.ok:
        finish(request_id, 'error', result.message or 'Failed to save Cookie.')
        return JSONResponse({'status': 'error', 'message': result.message or 'Failed to save Cookie.'}, status_code=422)
    finish(request_id, 'approved', 'Cookie synchronized successfully.')
    return JSONResponse({'status': 'success', 'message': 'Cookie synchronized successfully.'})


async def reject(request: Request):
    request_id = request.path_params['request_id']
    sync_request = finish(request_id, 'rejected', 'Cookie synchronization was rejected.')
    if sync_request is None:
        return JSONResponse({'status': 'error', 'message': 'Sync request expired or does not exist.'}, status_code=404)
    return JSONResponse({'status': 'success', 'message': sync_request.message})
