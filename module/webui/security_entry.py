"""One optional shared entry key for HTTP, downloads and WebSocket connections."""

import asyncio
import hmac
import logging
import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from starlette.background import BackgroundTask
from starlette.requests import HTTPConnection, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE_NAME = 'nkas_entry'
KEY_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')
PRIVATE_HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}


class EntryLogFilter(logging.Filter):
    def filter(self, record):
        def redact(value):
            if isinstance(value, str):
                return re.sub(r'(/entry/)[^\s?\x22\x27]+', r'\1[redacted]', value)
            return value
        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        return True


class SecurityEntry:
    def __init__(self, config):
        self.config = config
        self.key_file = Path(config.file).resolve().parent / '.security' / 'entry.key'
        self._key = None
        self._sockets = set()
        if self.enabled:
            self.ensure_key()
        logging.getLogger('uvicorn.access').addFilter(EntryLogFilter())

    @property
    def enabled(self):
        return self.config.SecurityEntryEnabled is True

    def ensure_key(self):
        if self._key is None:
            if self.key_file.exists():
                value = self.key_file.read_text(encoding='ascii').strip()
                if not KEY_PATTERN.fullmatch(value):
                    raise ValueError('Invalid security entry key file; restore it or disable SecurityEntryEnabled locally.')
                self._key = value
            else:
                self.regenerate()
        return self._key

    def regenerate(self):
        value = secrets.token_urlsafe(32)
        self.key_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.entry-', dir=self.key_file.parent)
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(value + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.key_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self._key = value
        return value

    def matches(self, value):
        return isinstance(value, str) and bool(KEY_PATTERN.fullmatch(value)) and hmac.compare_digest(
            self.ensure_key(), value)

    def authorized(self, connection):
        if not self.enabled:
            return True
        authorization = connection.headers.get('authorization', '')
        if authorization:
            scheme, _, value = authorization.partition(' ')
            return scheme.lower() == 'bearer' and self.matches(value)
        return self.matches(connection.cookies.get(COOKIE_NAME))

    def payload(self):
        key = self.ensure_key() if self.enabled else None
        return {'enabled': self.enabled, 'key': key, 'entry_path': f'/entry/{key}' if key else None}

    async def invalidate_sockets(self):
        for revoked in tuple(self._sockets):
            revoked.set()

    def response(self, request, *, value=None, changed=False):
        response = JSONResponse(
            {'status': 'success', 'value': value, 'security_entry': self.payload()},
            headers=PRIVATE_HEADERS,
            background=BackgroundTask(self.invalidate_sockets) if changed else None,
        )
        self.set_cookie(response, request)
        return response

    def set_cookie(self, response, connection):
        path = connection.scope.get('root_path', '').rstrip('/') or '/'
        if self.enabled:
            response.set_cookie(COOKIE_NAME, self.ensure_key(), httponly=True, samesite='strict',
                                secure=connection.url.scheme == 'https', path=path, max_age=31536000)
        else:
            response.delete_cookie(COOKIE_NAME, path=path)


def same_origin(connection):
    origin = connection.headers.get('origin')
    if not origin:
        return connection.headers.get('sec-fetch-site') != 'cross-site'
    parsed = urlsplit(origin)
    # Do not trust Forwarded/X-Forwarded-Host or exempt loopback: SSH tunnels
    # also arrive on loopback. Proxy deployments must preserve the Host header.
    return parsed.scheme in ('http', 'https') and parsed.netloc.lower() == connection.headers.get('host', '').lower()


def entry_required(status=401, html=False):
    if html:
        return HTMLResponse(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>需要安全入口</title>'
            '<style>body{font:16px/1.6 system-ui;max-width:640px;margin:12vh auto;padding:24px;'
            'color-scheme:light dark}h1{font-size:24px}</style>'
            '<h1>需要安全入口</h1><p>请使用部署页提供的最新完整安全入口地址访问。</p>'
            '<p>本机 exe 将自动恢复。远程 App 请在设置中重新粘贴完整入口。</p>'
            '<script>window.__TAURI__?.core?.invoke("refresh_security_entry").catch(()=>{});</script></html>',
            status_code=status, headers=PRIVATE_HEADERS,
        )
    return JSONResponse({'status': 'error', 'code': 'security_entry_required',
                         'message': '安全入口已开启或已更新，请使用完整安全入口重新连接。'},
                        status_code=status, headers=PRIVATE_HEADERS)


class SecurityEntryMiddleware:
    def __init__(self, app, security):
        self.app = app
        self.security = security

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        authorized = self.security.authorized(connection)
        scope.setdefault('state', {})['security_entry_authorized'] = authorized
        path = scope['path']
        if scope['type'] == 'http':
            extension_sync = path == '/api/cookie-sync/request' or path.startswith('/api/cookie-sync/status/')
            origin = connection.headers.get('origin', '')
            if extension_sync and origin.startswith(('chrome-extension://', 'moz-extension://')):
                if scope['method'] in ('GET', 'POST', 'OPTIONS'):
                    return await self.app(scope, receive, send)
            if path.startswith('/entry/'):
                # Entry URLs never reach application handlers or error pages.
                valid = scope['method'] in ('GET', 'HEAD') and self.security.enabled and self.security.matches(path[7:])
                if valid:
                    prefix = scope.get('root_path', '').rstrip('/')
                    response = RedirectResponse(f'{prefix}/app/', status_code=303, headers=PRIVATE_HEADERS)
                    self.security.set_cookie(response, connection)
                else:
                    response = HTMLResponse('<!doctype html><meta charset="utf-8"><title>安全入口无效</title>'
                                            '<h1>安全入口无效</h1><p>请使用部署页提供的最新完整入口地址。</p>',
                                            status_code=404, headers=PRIVATE_HEADERS)
                return await response(scope, receive, send)
            if not authorized and not (path == '/api/system/status' and scope['method'] == 'GET'):
                return await entry_required(html='text/html' in connection.headers.get('accept', ''))(scope, receive, send)
            if scope['method'] not in ('GET', 'HEAD', 'OPTIONS') and not same_origin(connection):
                return await JSONResponse({'message': 'Cross-origin request denied.'}, status_code=403)(scope, receive, send)
            return await self.app(scope, receive, send)

        if not authorized or (self.security.enabled and not same_origin(connection)):
            await send({'type': 'websocket.close', 'code': 4401})
            return
        revoked = asyncio.Event()
        self.security._sockets.add(revoked)
        application = asyncio.create_task(self.app(scope, receive, send))
        invalidation = asyncio.create_task(revoked.wait())
        try:
            done, _ = await asyncio.wait((application, invalidation), return_when=asyncio.FIRST_COMPLETED)
            if application in done:
                await application
            else:
                application.cancel()
                await asyncio.gather(application, return_exceptions=True)
                try:
                    await send({'type': 'websocket.close', 'code': 4401, 'reason': 'security_entry_changed'})
                except (OSError, RuntimeError):
                    pass  # The peer may have closed at the same time.
        finally:
            self.security._sockets.discard(revoked)
            application.cancel()
            invalidation.cancel()
            await asyncio.gather(application, invalidation, return_exceptions=True)


async def entry_info(request: Request):
    return request.app.state.security_entry.response(request)


async def regenerate_entry(request: Request):
    security = request.app.state.security_entry
    if not security.enabled:
        return JSONResponse({'message': '请先在部署页开启安全入口。'}, status_code=409)
    try:
        security.regenerate()
    except OSError:
        return JSONResponse({'message': '无法保存安全入口，请检查配置目录权限。'}, status_code=500)
    return security.response(request, changed=True)
