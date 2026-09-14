"""Isolated real security/deploy API + built SPA; never starts game tasks or updates.

Run with the project's Python: python tests/security_entry_fixture.py --port 18771
All writes go to a disposable directory, not the user's config/deploy.yaml.
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def create_app():
    directory = tempfile.TemporaryDirectory(prefix='nkas-security-web-')
    original_cwd = Path.cwd()
    sandbox = Path(directory.name)
    (sandbox / 'config').mkdir()
    (sandbox / 'deploy').mkdir()
    shutil.copyfile(ROOT / 'deploy/template', sandbox / 'deploy/template')
    os.chdir(sandbox)

    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse, RedirectResponse
    from starlette.routing import Mount, Route, WebSocketRoute
    from starlette.staticfiles import StaticFiles
    from starlette.websockets import WebSocketDisconnect
    from module.webui.config import DeployConfig
    from module.webui.setting import State
    from module.webui.security_entry import SecurityEntry, SecurityEntryMiddleware, entry_info, regenerate_entry

    config = DeployConfig(file=str(sandbox / 'config/deploy.yaml'))
    config.AutoUpdate = False
    config.CheckUpdateInterval = 0
    config.EnableReload = False
    config.Run = None
    State._deploy_config_ = config
    from module.webui.api import routes_deploy, routes_system
    routes_deploy.RESET_TEMPLATES = {
        key: str(ROOT / value.lstrip('./')) for key, value in routes_deploy.RESET_TEMPLATES.items()
    }

    def reply(value):
        async def endpoint(_):
            return JSONResponse(value)
        return endpoint

    async def home(_):
        return RedirectResponse('/app/')

    async def socket(websocket):
        await websocket.accept()
        await websocket.send_json({'type': 'fixture', 'connected': True})
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    security = SecurityEntry(config)
    app = Starlette(routes=[
        Route('/', home),
        Route('/api/system/status', routes_system.status),
        Route('/api/system/theme', routes_system.set_theme, methods=['POST']),
        Route('/api/system/deploy', routes_deploy.deploy_schema, methods=['GET']),
        Route('/api/system/deploy', routes_deploy.deploy_patch, methods=['PATCH']),
        Route('/api/system/deploy/reset', routes_deploy.deploy_reset, methods=['POST']),
        Route('/api/security/entry', entry_info),
        Route('/api/security/entry/regenerate', regenerate_entry, methods=['POST']),
        Route('/api/system/update', reply({'state': 'idle', 'history': []})),
        Route('/api/system/notices', reply({'notices': [], 'announcements': []})),
        Route('/api/instances', reply([])),
        Route('/api/avatars', reply([])),
        Route('/api/serial/state', reply({'enabled': False})),
        Route('/api/calendar', reply({'items': [], 'updated_at': 0})),
        Route('/api/maintenance', reply({'active': False})),
        Route('/api/protected', reply({'ok': True}), methods=['GET', 'POST']),
        Route('/api/nkas/screenshot', reply({'ok': True})),
        Route('/api/nkas/export', reply({'ok': True})),
        Route('/api/system/logs/download', reply({'ok': True})),
        Route('/scrcpy/nkas/', reply({'ok': True})),
        WebSocketRoute('/ws/{path:path}', socket),
        Mount('/app', StaticFiles(directory=str(ROOT / 'webui/dist'), html=True)),
        Mount('/avatars', StaticFiles(directory=str(ROOT / 'assets/gui/avatars'))),
        Mount('/static', StaticFiles(directory=str(ROOT / 'assets'))),
    ], middleware=[Middleware(SecurityEntryMiddleware, security=security)])
    app.state.security_entry = security
    app.state.fixture_directory = directory
    app.state.original_cwd = original_cwd
    return app


def cleanup(app):
    os.chdir(app.state.original_cwd)
    app.state.fixture_directory.cleanup()


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18771)
    args = parser.parse_args()
    application = create_app()
    try:
        uvicorn.run(application, host='127.0.0.1', port=args.port, proxy_headers=False)
    finally:
        cleanup(application)
