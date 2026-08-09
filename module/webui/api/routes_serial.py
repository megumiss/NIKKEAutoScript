"""串行执行（多实例顺序执行）的状态查询与手动重置。

串行的开关、组、出错行为、移交阈值走通用 deploy 配置接口
（/api/system/deploy，schema 从 deploy/template 解析），这里只提供运行状态。
"""

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.serial_state import get_due_at, get_state, modify_state, read_serial_config
from module.webui.process_manager import ProcessManager


async def state(_: Request):
    config = read_serial_config()
    s = get_state()
    instances = {}
    for name in config.group:
        manager = ProcessManager.get_manager(name)
        due = get_due_at(s, name)
        instances[name] = {
            'alive': manager.alive,
            'state': manager.state,
            'due_at': due.isoformat() if due else None,
            'failed': name in s['failed'],
            'retried': name in s['retried'],
            'current': s.get('current') == name,
            # 实例进程上报的等待令牌状态；进程已退出时忽略残留标记
            'waiting': bool(s['instances'].get(name, {}).get('waiting')) and manager.alive,
        }
    return JSONResponse({
        'enable': config.enable,
        'group': config.group,
        'on_error': config.on_error,
        'idle_threshold': config.idle_threshold,
        'current': s.get('current'),
        'cycle': s.get('cycle'),
        'halted': s.get('halted', False),
        'instances': instances,
    })


async def reset(_: Request):
    """
    手动重置：清除 halted / failed / retried 标记。
    用于 OnError=stop 停止串行后的人工恢复。
    """
    def _fn(s):
        s['halted'] = False
        s['failed'] = {}
        s['retried'] = []
    modify_state(_fn)
    return JSONResponse({'status': 'success'})
