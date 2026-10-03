"""Isolated deploy/CDK API and built SPA for browser simulations."""

import argparse

from security_entry_fixture import cleanup, create_app


def mirror_app():
    app = create_app()
    from pathlib import Path

    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from deploy.mirrorchyan import enabled
    from module.webui.api.routes_deploy import mirror_cdk
    from module.webui.setting import State

    root = Path(State.deploy_config.file).parent.parent
    object.__setattr__(State.deploy_config, 'root_filepath', str(root))
    state = {'state': 0, 'error': '', 'history': []}

    async def update_status(_):
        return JSONResponse({**state, 'channel': 'MirrorChyan' if enabled(root) else 'Git'})

    async def check_update(_):
        state.update(state='failed', error='有新版本，但 Mirror 酱未返回下载链接', failure_stage='check')
        return JSONResponse({'status': 'success'})

    app.router.routes = [route for route in app.router.routes if getattr(route, 'path', '') != '/api/system/update']
    app.router.routes.extend(
        [
            Route('/api/system/mirror-cdk', mirror_cdk, methods=['GET', 'POST']),
            Route('/api/system/update', update_status),
            Route('/api/update/check', check_update, methods=['POST']),
        ]
    )
    return app


if __name__ == '__main__':
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18772)
    args = parser.parse_args()
    app = mirror_app()
    try:
        uvicorn.run(app, host='127.0.0.1', port=args.port, proxy_headers=False)
    finally:
        cleanup(app)
