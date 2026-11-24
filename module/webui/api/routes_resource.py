"""资源仓库的状态查询与后台同步。"""

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from module.config.config_generated import GeneratedConfig
from module.config.deep import deep_get
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.resource import resource_manager
from module.webui.setting import State


def _repository(request: Request):
    """读取请求实例的自动推图仓库地址；旧的全局端点只使用任务默认地址。

    实例名必须先验证，防止读取任意配置路径；缺少新字段的旧实例由配置更新器补齐，
    不从部署配置恢复仓库地址，也不修改部署配置。
    """
    name = request.path_params.get('name')
    if not name:
        return GeneratedConfig.CampaignSettings_ResourceRepository
    validate_instance(name)
    config = State.config_updater.read_file(name)
    return deep_get(config, 'AutoMainStory.CampaignSettings.ResourceRepository',
                    default=GeneratedConfig.CampaignSettings_ResourceRepository)


async def status(request: Request):
    """查询地图资源的本地版本、远端版本与同步状态。

    访问远端要走网络，放到线程里执行；查询结果禁用缓存，否则同步进度会停在第一次的响应上。
    """
    # check=0 只读本地与缓存的远端信息，供同步过程中的轮询使用，避免每次都访问远端。
    check = request.query_params.get('check', '1') != '0'
    try:
        repository = _repository(request)
    except InstanceNotFound as exc:
        return JSONResponse({'error': str(exc)}, status_code=404)
    result = await asyncio.to_thread(resource_manager.status, check, repository)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


async def sync(request: Request):
    """触发一次后台资源同步，立即返回而不等待完成。

    同步在独立线程里跑，进度要由 status 轮询获取；已有同步在跑时返回 409 而不是再起一个，
    避免两个线程同时写 `data/resources/`。
    """
    try:
        repository = _repository(request)
    except InstanceNotFound as exc:
        return JSONResponse({'error': str(exc)}, status_code=404)
    if not resource_manager.start_sync(repository):
        return JSONResponse({'status': 'error', 'message': '资源同步正在进行。'}, status_code=409)
    return JSONResponse({'status': 'success'})
