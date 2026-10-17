"""实例章节观测及共享章节底图。"""

import asyncio

from starlette.responses import FileResponse, JSONResponse

from module.campaign.map.catalog import chapter_map
from module.webui.process_manager import ProcessManager
from .deps import InstanceNotFound, validate_instance


async def campaign(request):
    """返回指定实例最新的章节观测快照。

    快照由任务进程推送、进程管理器持有，实例没在跑时只有 running 为假而快照仍是上一次的值，
    界面据此区分「没有数据」和「数据已停更」。禁用缓存，避免轮询拿到旧快照。
    """
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'error': str(exc)}, status_code=404)
    manager = ProcessManager._processes.get(name)
    snapshot = manager.latest_campaign if manager else None
    running = bool(manager and manager.alive)
    return JSONResponse(dict(snapshot=snapshot, running=running),
                        headers={'Cache-Control': 'no-store'})


async def map_image(request):
    """按章节下发底图 PNG，供界面叠加小队、敌人与目标点。

    底图来自资源仓库同步的地图包，普通与困难共用，所以路径里只有章节号。
    读盘和地图包校验都放到线程里，避免阻塞事件循环；资源缺失一律按 404 处理，让界面退回无图显示。
    用 no-cache 而不是 no-store：底图会随资源同步更换，但同一版本可以走协商缓存。
    """
    try:
        validate_instance(request.path_params['name'])
        package, _ = await asyncio.to_thread(chapter_map, request.path_params['chapter'])
    except InstanceNotFound as exc:
        return JSONResponse({'error': str(exc)}, status_code=404)
    except (OSError, ValueError, KeyError):
        return JSONResponse({'error': 'Chapter map unavailable.'}, status_code=404)
    return FileResponse(package / 'map.png', media_type='image/png', headers={'Cache-Control': 'no-cache'})
