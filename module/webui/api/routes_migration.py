import asyncio
import threading
from contextlib import ExitStack
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from module.logger import logger
from module.webui.config_migration import MigrationError, migrate_files, plan_migration
from module.webui.process_manager import ProcessManager
from module.webui.setting import State

ROOT = Path(__file__).resolve().parents[3]
_migration_lock = threading.Lock()


class MigrationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Cached deploy settings and background services are refreshed on restart.
        if (scope['type'] == 'http' and State.config_migration_pending
                and scope['path'].startswith('/api/')
                and scope['path'] not in ('/api/system/status', '/api/security/entry')
                and (scope['path'] != '/api/restart' or _migration_lock.locked())):
            response = JSONResponse({'message': '配置迁移中或已完成，请重启程序后再修改配置或启动实例。'}, status_code=409)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _migrate(source, token):
    from deploy.update_lock import UpdateBusyError, UpdateLock
    from module.webui.serial_orchestrator import serial_orchestrator
    from module.webui.updater import updater

    was_running = serial_orchestrator._alive
    completed = False
    safe_to_resume = True
    try:
        with ExitStack() as locks:
            if not updater._operation_lock.acquire(blocking=False):
                raise MigrationError('更新正在进行，请稍后重试。')
            locks.callback(updater._operation_lock.release)
            try:
                locks.enter_context(UpdateLock(ROOT))
            except UpdateBusyError as exc:
                raise MigrationError('另一个源码或启动器更新正在运行，或更新锁不可写。') from exc
            plan = plan_migration(source, ROOT)
            if plan['token'] != token:
                raise MigrationError('迁移文件发生变化，请重新预览后确认。')
            # Stop the orchestrator before locking lifecycles: retries also take those locks.
            serial_orchestrator.stop()
            if serial_orchestrator._thread is not None:
                serial_orchestrator._thread.join(timeout=10)
                if serial_orchestrator._thread.is_alive():
                    raise MigrationError('串行编排器尚未停止，请稍后重试。')
            with ProcessManager._processes_lock:
                managers = list(ProcessManager._processes.values())
            for manager in managers:
                locks.enter_context(manager.lifecycle_lock)
            if any(manager.alive for manager in managers):
                raise MigrationError('请先停止当前部署的所有实例。')
            migrate_files(plan)
            completed = True
    except MigrationError as exc:
        safe_to_resume = not exc.restart_required
        raise
    finally:
        if was_running and not completed and safe_to_resume:
            serial_orchestrator.start(ev=serial_orchestrator._event)
    logger.info(f'Config migration completed: {plan["file_count"]} files')
    return {'status': 'success', 'file_count': plan['file_count'], 'restart_required': True}


async def migrate(request):
    try:
        data = await request.json()
        if not isinstance(data, dict) or data.get('action') not in ('preview', 'migrate'):
            raise MigrationError('请选择预览或迁移操作。')
        if State.config_migration_pending:
            raise MigrationError('请重启程序后再进行迁移。')
        if data['action'] == 'preview':
            plan = await run_in_threadpool(plan_migration, data.get('source'), ROOT)
            plan.pop('files')
            return JSONResponse(plan)
        if not isinstance(data.get('token'), str):
            raise MigrationError('请先预览迁移内容。')
        if not _migration_lock.acquire(blocking=False):
            return JSONResponse({'message': '配置迁移正在进行，请勿重复提交。'}, status_code=409)
        State.config_migration_pending = True
        # A disconnected browser must not release the guard while copying continues.
        task = asyncio.create_task(asyncio.to_thread(_migrate, data.get('source'), data['token']))
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            return JSONResponse(task.result())
        except (OSError, ValueError) as exc:
            State.config_migration_pending = getattr(exc, 'restart_required', False)
            raise
        finally:
            _migration_lock.release()
    except (OSError, ValueError) as exc:
        logger.warning(f'Config migration failed: {exc}')
        return JSONResponse({'message': str(exc)}, status_code=400)
