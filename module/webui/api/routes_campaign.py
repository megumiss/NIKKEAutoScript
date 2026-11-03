"""实例章节观测及共享章节底图。"""

import asyncio
from pathlib import Path

from markdown_it import MarkdownIt

from starlette.responses import FileResponse, JSONResponse

from module.campaign.map.catalog import chapter_map
from module.logger import logger
from module.webui.process_manager import ProcessManager
from .deps import InstanceNotFound, validate_instance


NOTICE_DIRECTORY = Path(__file__).resolve().parents[2] / 'campaign' / 'notice'
NOTICE_LANGUAGES = ('zh-CN', 'en-US', 'ja-JP')


def read_notice(language='zh-CN'):
    """读取独立的主线注意事项 Markdown，并拆分标题、摘要和安全渲染的详细正文。

    只允许固定语言文件，未知语言及缺失翻译回退中文，不接受任意路径。文档以一级标题和
    一个摘要段落开头，余下 Markdown 作为展开正文；每次请求重新读盘，不缓存文案。
    使用现有 markdown-it 依赖，禁用原始 HTML 和图片，避免执行脚本或自动加载远端图片。
    文件错误、格式错误向路由传播，由路由隐藏提示而不影响推图配置或任务控制。
    """
    language = language if language in NOTICE_LANGUAGES else 'zh-CN'
    path = NOTICE_DIRECTORY / f'{language}.md'
    if not path.is_file() and language != 'zh-CN':
        path = NOTICE_DIRECTORY / 'zh-CN.md'
    content = path.read_text(encoding='utf-8-sig')
    renderer = MarkdownIt('commonmark', {'html': False}).disable('image')
    tokens = renderer.parse(content)
    if (len(tokens) < 6 or tokens[0].type != 'heading_open' or tokens[0].tag != 'h1'
            or tokens[1].type != 'inline' or tokens[3].type != 'paragraph_open'
            or tokens[4].type != 'inline' or tokens[5].type != 'paragraph_close'):
        raise ValueError('注意事项必须以一级标题和摘要段落开头。')
    title = tokens[1].content.strip()
    summary = ''.join(' ' if token.type in ('softbreak', 'hardbreak') else token.content
                      for token in tokens[4].children
                      if token.type in ('text', 'code_inline', 'softbreak', 'hardbreak'))
    if not title or not summary.strip():
        raise ValueError('注意事项标题或摘要为空。')
    return dict(title=title, summary=summary.strip(), details_html=renderer.renderer.render(tokens[6:],
                renderer.options, {}))


async def notice(request):
    """下发按界面语言读取的主线注意事项，编辑 Markdown 后刷新页面即可生效。

    文件读取与 Markdown 渲染放在线程中，响应禁止缓存；缺失或畸形文案只关闭提示栏，
    不影响同页设置和启动按钮。语言严格映射至仓库自带文件，不暴露任意文件读取。
    """
    try:
        content = await asyncio.to_thread(read_notice, request.query_params.get('language', 'zh-CN'))
    except (OSError, UnicodeError, ValueError) as exc:
        logger.warning(f'自动主线注意事项不可用，隐藏提示栏：{exc}')
        content = None
    return JSONResponse({'notice': content}, headers={'Cache-Control': 'no-store'})


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
