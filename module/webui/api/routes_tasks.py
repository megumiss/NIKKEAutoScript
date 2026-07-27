from starlette.requests import Request
from starlette.responses import JSONResponse

import module.webui.lang as lang
from module.submodule.utils import load_config
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.api.models import QueueInfo
from module.webui.process_manager import ProcessManager
from module.webui.updater import updater
from module.webui.api.service_tasks import TaskService


def queue_data(name):
    config = load_config(name)
    config.load()
    config.get_next_task()
    manager = ProcessManager.get_manager(name)

    def item(func):
        command = func.command
        return {'command': command, 'next_run': str(func.next_run), 'name_i18n': lang.t(f'Task.{command}.name')}

    pending = list(config.pending_task)
    running = pending[:1] if manager.alive else []
    pending = pending[1:] if manager.alive else pending
    return QueueInfo([item(task) for task in running], [item(task) for task in pending],
                     [item(task) for task in config.waiting_task]).dict()


async def queue(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
        return JSONResponse(queue_data(name))
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)


async def run_task(request: Request):
    name, task = request.path_params['name'], request.path_params['task']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    result = TaskService().run_now(name, task)
    if result is None:
        return JSONResponse({'status': 'error', 'message': f'No configured task named {task}.'}, status_code=404)
    return JSONResponse({'status': 'success', 'message': f'{task} queued for immediate execution.', **result})


async def start_tool(request: Request):
    name, task = request.path_params['name'], request.path_params['task']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    result = TaskService().start_tool(name, task)
    if result is None:
        return JSONResponse({'status': 'error', 'message': f'No tool task named {task}.'}, status_code=404)
    if result.get('already_running'):
        return JSONResponse({'status': 'error', 'message': 'Stop the current process before starting a tool task.'}, status_code=409)
    return JSONResponse({'status': 'success', 'message': f'{task} started.', **result})
