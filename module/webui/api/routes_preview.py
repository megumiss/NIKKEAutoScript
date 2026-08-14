"""In-memory game screen preview frames."""

import posixpath
from urllib.parse import quote, urlparse

import httpx
from starlette.responses import HTMLResponse, JSONResponse, Response

from module.webui.api.deps import InstanceNotFound, load_instance_config
from module.webui.process_manager import ProcessManager

# ws-scrcpy 设备端 WS 服务端口（其前端 bundle 内常量 SERVER_PORT），
# 流地址里的 ws 参数必须是 proxy-adb 形式的完整 WebSocket URL，否则其前端启动即报错白屏。
SCRCPY_WS_SERVER_PORT = 8886


async def screenshot(request):
    """Return the latest preview frame published by the instance worker."""
    name = request.path_params['name']
    # Avoid get_manager() here: it would create a manager for unknown names.
    manager = ProcessManager._processes.get(name)
    preview = manager.latest_preview if manager else None
    if preview is None:
        return JSONResponse({'error': 'no preview'}, status_code=404)
    captured_at, data = preview
    return Response(
        content=data,
        media_type='image/jpeg',
        headers={'X-Captured-At': str(captured_at)},
    )


async def scrcpy(request):
    """Direct-stream URL of the external ws-scrcpy service for this instance.

    Always 200 with {available, url?, reason?} so the SPA can grey out the
    control button with an explanation instead of hiding it silently.
    """
    name = request.path_params['name']
    try:
        config = load_instance_config(name)
    except InstanceNotFound:
        return JSONResponse({'available': False, 'reason': 'not_configured'})
    base = str(config.Emulator_ScrcpyWebUrl or '').strip()
    if not base:
        return JSONResponse({'available': False, 'reason': 'not_configured'})
    if config.Client_Platform != 'adb':
        return JSONResponse({'available': False, 'reason': 'win_platform'})
    serial = str(config.Emulator_Serial or '')
    if serial == 'auto':
        return JSONResponse({'available': False, 'reason': 'serial_auto'})
    base_url = base.rstrip('/')
    parsed = urlparse(base_url)
    ws_scheme = 'wss' if parsed.scheme == 'https' else 'ws'
    # 与其前端设备列表生成的链接保持一致（player 用 broadway：纯软件解码，兼容性最好）
    ws_url = (
        f'{ws_scheme}://{parsed.netloc}/?action=proxy-adb'
        f'&remote=tcp%3A{SCRCPY_WS_SERVER_PORT}&udid={quote(serial, safe="")}'
    )
    hash_params = f'action=stream&udid={quote(serial, safe="")}&player=broadway&ws={quote(ws_url, safe="")}'
    url = f'{base_url}/#!{hash_params}'
    return JSONResponse({'available': True, 'url': url})


async def scrcpy_page(request):
    """Same-origin proxy of the ws-scrcpy index page.

    Served at /scrcpy/{name}/ WITHOUT a <base> tag: relative asset URLs
    (bundle.js, avc.wasm, …) then resolve to /scrcpy/{name}/<asset> and are
    forwarded by scrcpy_asset below, keeping everything same-origin. This
    matters because Broadway's decoder fetches avc.wasm via fetch(), which
    is CORS-blocked cross-origin; script/img tags are not. The video
    WebSocket keeps connecting directly to upstream (the ws hash param),
    so no WS tunneling is needed. Being same-origin also lets the preview
    card read the real canvas size and inject layout CSS.
    """
    name = request.path_params['name']
    base = _scrcpy_base(name)
    if base is None:
        return JSONResponse({'error': 'not configured'}, status_code=404)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f'{base}/')
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return JSONResponse({'error': f'upstream unreachable: {e}'}, status_code=502)
    return HTMLResponse(resp.text)


async def scrcpy_asset(request):
    """Forward a static asset of the ws-scrcpy page from upstream."""
    name = request.path_params['name']
    asset = posixpath.normpath(request.path_params['asset'])
    if asset.startswith('..') or asset.startswith('/') or asset == '.':
        return JSONResponse({'error': 'invalid path'}, status_code=400)
    base = _scrcpy_base(name)
    if base is None:
        return JSONResponse({'error': 'not configured'}, status_code=404)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f'{base}/{asset}')
    except httpx.HTTPError as e:
        return JSONResponse({'error': f'upstream unreachable: {e}'}, status_code=502)
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get('content-type'),
    )


def _scrcpy_base(name: str):
    try:
        config = load_instance_config(name)
    except InstanceNotFound:
        return None
    base = str(config.Emulator_ScrcpyWebUrl or '').strip().rstrip('/')
    return base or None

