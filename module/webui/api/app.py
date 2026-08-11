"""Route registration and static SPA mount."""

from pathlib import Path

from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from module.logger import logger
from . import (routes_calendar, routes_config, routes_deploy, routes_instances, routes_logs, routes_maintenance,
               routes_notify, routes_stats, routes_system, routes_tasks, ws)


def create_spa_mount():
    dist = Path(__file__).resolve().parents[3] / 'webui' / 'dist'
    if dist.is_dir() and (dist / 'index.html').is_file():
        return Mount('/app', app=StaticFiles(directory=str(dist), html=True), name='spa')
    logger.warning(f'SPA build output not found: {dist}')
    async def missing(_):
        return HTMLResponse('<h1>NKAS UI is not built</h1><p>Please update the application completely.</p>', status_code=503)
    return Route('/app/{path:path}', missing)


def mount_api(app):
    routes = [
        Route('/api/instances', routes_instances.instances, methods=['GET']),
        Route('/api/instances', routes_instances.create, methods=['POST']),
        Route('/api/instances/import', routes_instances.import_config, methods=['POST']),
        Route('/api/instances/order', routes_instances.reorder, methods=['POST']),
        Route('/api/instances/{name:str}/rename', routes_instances.rename, methods=['POST']),
        Route('/api/calendar', routes_calendar.calendar, methods=['GET']),
        Route('/api/maintenance', routes_maintenance.maintenance, methods=['GET']),
        Route('/api/restart', routes_system.restart, methods=['POST']),
        Route('/api/update', routes_system.update, methods=['POST']),
        Route('/api/update/check', routes_system.check_update, methods=['POST']),
        Route('/api/rotate', routes_system.rotate, methods=['POST']),
        Route('/api/system/status', routes_system.status, methods=['GET']),
        Route('/api/system/update', routes_system.update_status, methods=['GET']),
        Route('/api/system/remote', routes_system.remote_status, methods=['GET']),
        Route('/api/system/notices', routes_system.notices, methods=['GET']),
        Route('/api/system/notices/read', routes_system.read_announcements, methods=['POST']),
        Route('/api/system/notices/{key:str}/dismiss', routes_system.dismiss_notice, methods=['POST']),
        Route('/api/system/monitors', routes_system.monitors, methods=['GET']),
        Route('/api/system/pick-path', routes_system.pick_path, methods=['POST']),
        Route('/api/system/language', routes_system.set_language, methods=['POST']),
        Route('/api/system/theme', routes_system.set_theme, methods=['POST']),
        Route('/api/system/deploy', routes_deploy.deploy_schema, methods=['GET']),
        Route('/api/system/deploy', routes_deploy.deploy_patch, methods=['PATCH']),
        Route('/api/system/deploy/reset', routes_deploy.deploy_reset, methods=['POST']),
        Route('/api/system/logs/files', routes_logs.log_files, methods=['GET']),
        Route('/api/system/logs', routes_logs.log_query, methods=['GET']),
        Route('/api/{name:str}/start', routes_instances.start, methods=['POST']),
        Route('/api/{name:str}/stop', routes_instances.stop, methods=['POST']),
        Route('/api/{name:str}/remark', routes_instances.remark, methods=['POST']),
        Route('/api/{name:str}/export', routes_instances.export, methods=['GET']),
        Route('/api/{name:str}/schema', routes_config.schema, methods=['GET']),
        Route('/api/{name:str}/config', routes_config.config, methods=['GET']),
        Route('/api/{name:str}/config', routes_config.patch_config, methods=['PATCH', 'POST']),
        Route('/api/{name:str}/queue', routes_tasks.queue, methods=['GET']),
        Route('/api/{name:str}/warehouse', routes_stats.warehouse, methods=['GET']),
        Route('/api/{name:str}/interception/stats', routes_stats.interception_stats, methods=['GET']),
        Route('/api/{name:str}/interception/import', routes_stats.import_interception, methods=['POST']),
        Route('/api/{name:str}/notify/test', routes_notify.test_notify, methods=['POST']),
        Route('/api/{name:str}/task/{task:str}/run', routes_tasks.run_task, methods=['POST']),
        Route('/api/{name:str}/tool/{task:str}/start', routes_tasks.start_tool, methods=['POST']),
        Route('/api/{name:str}', routes_instances.delete, methods=['DELETE']),
        WebSocketRoute('/ws/state', ws.state_socket),
        WebSocketRoute('/ws/{name:str}/log', ws.log_socket),
        WebSocketRoute('/ws/{name:str}/queue', ws.queue_socket),
        create_spa_mount(),
    ]
    app.router.routes.extend(routes)
