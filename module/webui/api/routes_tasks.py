import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

import module.webui.lang as lang
from datetime import datetime

from module.config.config import Function
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.api.models import QueueInfo
from module.webui.process_manager import START_GONE, START_STARTED, ProcessManager
from module.webui.updater import updater
from module.webui.setting import State
from module.webui.api.service_tasks import TaskService


def queue_data(name):
    data = State.config_updater.read_file(name)
    now = datetime.now()
    pending, waiting, invalid = [], [], []
    for task_data in data.values():
        task = Function(task_data)
        if not task.enable:
            continue
        if not isinstance(task.next_run, datetime):
            invalid.append(task)
        elif task.next_run < now:
            pending.append(task)
        else:
            waiting.append(task)
    # Order both queues the way NikkeConfig.get_next_task does, so the list
    # the user sees is the order the scheduler will actually run: due tasks
    # follow SCHEDULER_PRIORITY (config order is meaningless here, and it is
    # what pushed Notify to the top), future tasks sort by run time.
    from module.base.filter import Filter
    from module.config.manual_config import ManualConfig
    priority = Filter(regex=r"(.*)", attr=["command"])
    priority.load(ManualConfig.SCHEDULER_PRIORITY)
    pending = invalid + priority.apply(pending) if pending else invalid
    waiting = priority.apply(waiting)
    waiting.sort(key=lambda task: task.next_run)
    manager = ProcessManager.get_manager(name)

    def item(func):
        command = func.command
        return {'command': command, 'next_run': str(func.next_run), 'name_i18n': lang.t(f'Task.{command}.name')}

    running = pending[:1] if manager.alive else []
    pending = pending[1:] if manager.alive else pending
    return QueueInfo([item(task) for task in running], [item(task) for task in pending],
                     [item(task) for task in waiting]).dict()


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
    service = TaskService()
    # The config read-modify-write stays on the event loop, serialized with the
    # other Web config writers; only the start may wait for a stop cleanup.
    if not service.queue_now(name, task):
        return JSONResponse({'status': 'error', 'message': f'No configured task named {task}.'}, status_code=404)
    result = await asyncio.to_thread(service.ensure_scheduler, name)
    if result == START_GONE:
        # Deleted or renamed while this request waited; the queued change went with it.
        return JSONResponse({'status': 'error', 'message': f'Instance "{name}" was renamed or deleted.'},
                            status_code=404)
    return JSONResponse({'status': 'success', 'message': f'{task} queued for immediate execution.',
                         'task': task, 'started_scheduler': result == START_STARTED})


async def start_tool(request: Request):
    name, task = request.path_params['name'], request.path_params['task']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    result = await asyncio.to_thread(TaskService().start_tool, name, task)
    if result is None:
        return JSONResponse({'status': 'error', 'message': f'No tool task named {task}.'}, status_code=404)
    if result.get('gone'):
        return JSONResponse({'status': 'error', 'message': f'Instance "{name}" was renamed or deleted.'},
                            status_code=404)
    if result.get('already_running'):
        return JSONResponse({'status': 'error', 'message': 'Stop the current process before starting a tool task.'}, status_code=409)
    return JSONResponse({'status': 'success', 'message': f'{task} started.', **result})
