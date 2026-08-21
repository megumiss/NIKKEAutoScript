"""执行时间管理页接口：集中查看/修改各任务的调度周期与时间。"""

from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

import module.webui.lang as lang
from module.config.delay import next_month_day, next_weekday
from module.config.manual_config import ManualConfig
from module.config.utils import deep_get, deep_set, filepath_args, get_server_next_update, read_file
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.setting import State
from module.webui.utils import re_fullmatch

CADENCES = ('daily', 'weekly', 'monthly')

# 管理页字段名 -> Scheduler 配置键，按周期分组；校验正则从 args.json 读取（单一事实源）
CADENCE_FIELDS = {
    'daily': {'daily_times': 'ServerUpdate'},
    'weekly': {'weekly_days': 'WeeklyDay', 'weekly_time': 'WeeklyTime'},
    'monthly': {'monthly_day': 'MonthlyDay', 'monthly_time': 'MonthlyTime'},
}


def _load_args():
    return read_file(filepath_args('args', 'nkas'))


def schedule_data(name):
    data = State.config_updater.read_file(name)
    tasks = []
    for command, task_data in data.items():
        if not isinstance(task_data, dict):
            continue
        sch = task_data.get('Scheduler')
        if not isinstance(sch, dict):
            continue
        tasks.append({
            'command': command,
            'name_i18n': lang.t(f'Task.{command}.name'),
            'cadence': str(sch.get('Cadence', 'daily')),
            'cadence_locked': command in ManualConfig.SCHEDULE_LOCKED_TASKS,
            'next_run': str(sch.get('NextRun')),
            # 全量返回各周期字段，便于前端切换周期时直接编辑草稿
            'daily_times': str(sch.get('ServerUpdate', '04:00')),
            'weekly_days': str(sch.get('WeeklyDay', '2')),
            'weekly_time': str(sch.get('WeeklyTime', '04:00')),
            'monthly_day': str(sch.get('MonthlyDay', '1')),
            'monthly_time': str(sch.get('MonthlyTime', '04:00')),
        })
    # 与调度器一致的熟悉顺序
    priority = [t.strip() for t in ManualConfig.SCHEDULER_PRIORITY.split('>') if t.strip()]
    order = {command: index for index, command in enumerate(priority)}
    tasks.sort(key=lambda item: order.get(item['command'], len(order)))
    return tasks


async def schedule(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
        return JSONResponse({'status': 'success', 'tasks': schedule_data(name)})
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)


def _compute_next_run(cadence: str, sch: dict) -> datetime:
    if cadence == 'weekly':
        return next_weekday(sch['WeeklyDay'], sch['WeeklyTime'])
    if cadence == 'monthly':
        return next_month_day(sch['MonthlyDay'], sch['MonthlyTime'])
    return get_server_next_update(sch['ServerUpdate'])


async def save_schedule(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({'status': 'error', 'message': 'Invalid JSON body.'}, status_code=400)
    changes = body.get('changes')
    if not isinstance(changes, list) or not changes:
        return JSONResponse({'status': 'error', 'message': 'Empty changes.'}, status_code=400)

    args = _load_args()
    config = State.config_updater.read_file(name)

    # 先整体校验，任一非法则不落盘
    errors = {}
    pending = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        command = str(change.get('command', ''))
        sch_args = deep_get(args, f'{command}.Scheduler')
        sch_config = deep_get(config, f'{command}.Scheduler')
        if not isinstance(sch_args, dict) or not isinstance(sch_config, dict):
            errors[command] = '未知任务'
            continue

        cadence = change.get('cadence')
        current_cadence = str(sch_config.get('Cadence', 'daily'))
        if cadence is None:
            cadence = current_cadence
        cadence = str(cadence)
        if cadence not in CADENCES:
            errors[command] = f'未知周期: {cadence}'
            continue
        if cadence != current_cadence and command in ManualConfig.SCHEDULE_LOCKED_TASKS:
            errors[command] = '该任务不支持修改周期'
            continue

        # 只校验/修改所提供、且属于目标周期的字段
        field_map = CADENCE_FIELDS[cadence]
        fields = {}
        for api_key, config_key in field_map.items():
            value = change.get(api_key)
            if value is None or not str(value).strip():
                continue
            fields[config_key] = str(value).strip()
        for config_key, value in fields.items():
            pattern = deep_get(sch_args, f'{config_key}.validate')
            if pattern and not re_fullmatch(pattern, value):
                errors[command] = f'时间格式不正确: {value}'
                break
        else:
            pending.append((command, cadence, fields))

    if errors:
        return JSONResponse({'status': 'error', 'message': '校验失败', 'errors': errors}, status_code=422)

    now = datetime.now()
    applied = []
    for command, cadence, fields in pending:
        deep_set(config, f'{command}.Scheduler.Cadence', cadence)
        for config_key, value in fields.items():
            deep_set(config, f'{command}.Scheduler.{config_key}', value)
        # 下一次运行时间在未来（非待执行）时按新周期/时间重排；已到期任务本轮先跑，跑完自动采用新配置
        sch = config[command]['Scheduler']
        next_run = sch.get('NextRun')
        if isinstance(next_run, datetime) and next_run > now:
            sch['NextRun'] = _compute_next_run(cadence, sch).replace(microsecond=0)
        applied.append(command)

    State.config_updater.write_file(name, config)
    return JSONResponse({'status': 'success', 'applied': applied})
