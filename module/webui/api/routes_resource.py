"""资源仓库的状态查询与后台同步。"""

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.webui.resource import resource_manager


async def status(request: Request):
    """查询地图资源的本地版本、远端版本与同步状态。

    访问远端要走网络，放到线程里执行；查询结果禁用缓存，否则同步进度会停在第一次的响应上。
    """
    # check=0 只读本地与缓存的远端信息，供同步过程中的轮询使用，避免每次都访问远端。
    check = request.query_params.get('check', '1') != '0'
    result = await asyncio.to_thread(resource_manager.status, check)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


async def sync(_: Request):
    """触发一次后台资源同步，立即返回而不等待完成。

    同步在独立线程里跑，进度要由 status 轮询获取；已有同步在跑时返回 409 而不是再起一个，
    避免两个线程同时写 `data/resources/`。
    """
    if not resource_manager.start_sync():
        return JSONResponse({'status': 'error', 'message': '资源同步正在进行。'}, status_code=409)
    return JSONResponse({'status': 'success'})
