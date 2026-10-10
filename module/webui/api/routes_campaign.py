"""实例章节观测及共享章节底图。"""

import asyncio

from starlette.responses import FileResponse, JSONResponse

from module.campaign.maps import chapter_map
from module.webui.process_manager import ProcessManager
from .deps import InstanceNotFound, validate_instance


async def campaign(request):
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
    try:
        validate_instance(request.path_params['name'])
        package, _ = await asyncio.to_thread(chapter_map, request.path_params['chapter'])
    except InstanceNotFound as exc:
        return JSONResponse({'error': str(exc)}, status_code=404)
    except (OSError, ValueError, KeyError):
        return JSONResponse({'error': 'Chapter map unavailable.'}, status_code=404)
    return FileResponse(package / 'map.png', media_type='image/png', headers={'Cache-Control': 'no-cache'})
