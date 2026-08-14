"""In-memory game screen preview frames."""

import json
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
    base = str(config.Scrcpy_WebUrl or '').strip()
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
        config = load_instance_config(name)
    except InstanceNotFound:
        return JSONResponse({'error': 'not configured'}, status_code=404)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f'{base}/')
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return JSONResponse({'error': f'upstream unreachable: {e}'}, status_code=502)
    html = resp.text
    serial = str(config.Emulator_Serial or '')
    if serial and serial != 'auto':
        bitrate = _positive_int(config.Scrcpy_Bitrate, DEFAULT_SCRCPY_BITRATE)
        max_fps = _positive_int(config.Scrcpy_MaxFps, DEFAULT_SCRCPY_MAX_FPS)
        primer = _settings_primer_script(serial, bitrate, max_fps)
        html = html.replace('<script defer', primer + '<script defer', 1)
    return HTMLResponse(html)


# ws-scrcpy 前端把每台设备的视频设置存在 localStorage，key 形如
# `播放器名:udid:窗口宽x高`，流启动（bundle.js，defer）时读取；其 broadway
# 默认仅 480x480/2Mbps，一打开很糊，且 URL hash 不支持码率参数。这里在
# bundle.js 之前注入一段内联脚本，按 iframe 当前真实视口尺寸预写实例配置
# （Scrcpy 组）里的码率/帧率（从父页面写会差 1-2px 导致 key 不匹配）。
# 用户在 ⋮ 菜单手动保存的设置写在带 displayId/分辨率的完整 key 下，
# 优先级更高，不会被这份默认值覆盖。
DEFAULT_SCRCPY_BITRATE = 16000000
DEFAULT_SCRCPY_MAX_FPS = 60


def _positive_int(value, default):
    """用户可能把输入框清空（None/''）或填非法值，此时回退默认值。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _settings_primer_script(serial: str, bitrate: int, max_fps: int) -> str:
    key_prefix = json.dumps(f'BroadwayDecoder:{serial}:')
    settings = json.dumps({
        'displayId': 0,
        'bitrate': bitrate,
        'maxFps': max_fps,
        'iFrameInterval': 5,
        'bounds': {'width': 1280, 'height': 1280},
        'lockedVideoOrientation': -1,
        'sendFrameMeta': False,
    }, separators=(',', ':'))
    return (
        '<script>(function(){try{'
        f'localStorage.setItem({key_prefix}+window.innerWidth+"x"+window.innerHeight,{json.dumps(settings)});'
        '}catch(e){}})();</script>'
    )


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
    base = str(config.Scrcpy_WebUrl or '').strip().rstrip('/')
    return base or None

