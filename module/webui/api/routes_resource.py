"""资源仓库的状态查询与后台同步。"""

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.webui.resource import resource_manager


async def status(request: Request):
    # check=0 只读本地与缓存的远端信息，供同步过程中的轮询使用，避免每次都访问远端。
    check = request.query_params.get('check', '1') != '0'
    result = await asyncio.to_thread(resource_manager.status, check)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


async def sync(_: Request):
    if not resource_manager.start_sync():
        return JSONResponse({'status': 'error', 'message': '资源同步正在进行。'}, status_code=409)
    return JSONResponse({'status': 'success'})
