"""BlaAuth 自动登录：无头浏览器填表 + Web UI 内嵌滑块交互；Cookie 直传机器人绑定。"""

import json
import re
import threading
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from module.config.deep import deep_get
from module.logger import logger
from module.webui.api.deps import InstanceNotFound, validate_instance
from module.webui.api.service_config import ConfigService
from module.webui.setting import State

_session = None
_session_lock = threading.Lock()

# 绑定链接形如 <origin>/bind/<token>，token 正则与插件服务端 TOKEN_RE 保持一致
BIND_PATH_RE = re.compile(r'^/bind/([A-Za-z0-9_-]{32,128})$')
REQUIRED_BIND_COOKIES = ('game_token', 'game_uid', 'game_openid')
BIND_TIMEOUT = 30

# 终态：允许发起新会话
TERMINAL_STATES = {'idle', 'success', 'cancelled', 'timeout', 'error'}


def _current_session():
    with _session_lock:
        return _session


def _run_session(name: str, session):
    """线程入口：跑登录流程，成功后写回配置"""
    import logging

    from module.logger import (
        WEB_THEME,
        DailyFileHandler,
        Highlighter,
        HTMLConsole,
        RichRenderableHandler,
        file_formatter,
        web_formatter,
    )
    from module.webui.process_manager import ProcessManager

    # 登录跑在 GUI 进程，日志默认只进 gui 日志、不进实例的日志流。
    # 给会话线程单独挂两个 handler（按线程过滤，不混 GUI 进程其他日志）：
    # 实时日志 broker（Web UI 日志窗口经 websocket 即时可见）+ 实例日志文件
    # ./log/<date>_<name>.txt（与任务日志同格式，日志查看器可查）
    thread_id = threading.get_ident()
    handlers = []

    manager = ProcessManager.get_manager(name)

    def publish(item):
        levelno, log = item
        manager.renderables.append((levelno, log))
        manager._trim_renderables()
        manager._publish_log(log)

    live_handler = RichRenderableHandler(
        func=publish,
        console=HTMLConsole(
            force_terminal=False, force_interactive=False, width=80,
            color_system='truecolor', markup=False, safe_box=False,
            highlighter=Highlighter(), theme=WEB_THEME,
        ),
        show_path=False, show_time=False, show_level=True,
        rich_tracebacks=True, tracebacks_show_locals=False, tracebacks_extra_lines=1,
        highlighter=Highlighter(),
    )
    live_handler.setFormatter(web_formatter)
    live_handler.setLevel(logging.INFO)
    handlers.append(live_handler)

    file_handler = DailyFileHandler(name, encoding='utf-8')
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.INFO)
    handlers.append(file_handler)

    for handler in handlers:
        handler.addFilter(lambda record: record.thread == thread_id)
        logger.addHandler(handler)
    try:
        try:
            session.run()
        except Exception as e:
            logger.error(f'Bla login session thread exception: {e}')
        if not session.result:
            return
        expire = session.result.get('expire') or 0
        expire_text = ''
        if expire:
            expire_text = datetime.fromtimestamp(expire).astimezone().strftime('%Y-%m-%d %H:%M:%S %z')
        service = ConfigService()
        for key, value in (
            ('BlaAuth.BlaAuth.Cookie', session.result['cookie']),
            ('BlaAuth.BlaAuth.XCommonParams', session.result['xcommonparams']),
            ('BlaAuth.BlaAuth.LoginUser', session.result.get('username') or ''),
            ('BlaAuth.BlaAuth.TokenExpire', expire_text),
        ):
            result = service.patch(name, key, value)
            if not result.ok:
                logger.error(f'Failed to save {key}: {result.error}')
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


async def login_start(request: Request):
    global _session
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    if _current_session() is not None and _current_session().state not in TERMINAL_STATES:
        return JSONResponse({'status': 'error', 'message': 'A login session is already running.'}, status_code=409)

    from module.blablalink.login import BlaLoginSession
    from module.blablalink.renew import server_from_client
    from module.config.account import load_account

    account, password = load_account(name)
    if not account or not password:
        return JSONResponse(
            {'status': 'error', 'message': 'LiPass account not configured, please fill in account settings first.'},
            status_code=400,
        )

    config = State.config_updater.read_file(name)
    user_agent = deep_get(config, 'BlaAuth.BlaAuth.UserAgent', '') or ''
    language = 'zh-TW'
    try:
        params = json.loads(deep_get(config, 'BlaAuth.BlaAuth.XCommonParams', '') or '{}')
        language = params.get('language') or 'zh-TW'
    except (TypeError, ValueError):
        pass
    # 区服按实例客户端设置推导（PC 端取 PCClientInfo.Client，模拟器取包名），
    # 首次登录没有 XCommonParams 也有依据
    server = server_from_client(config)

    session = BlaLoginSession(account=account, password=password, user_agent=user_agent,
                              language=language, server=server)
    with _session_lock:
        _session = session
    threading.Thread(target=_run_session, args=(name, session), daemon=True).start()
    logger.info(f'Bla login session started for {name}')
    return JSONResponse({'status': 'success'})


async def login_status(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    session = _current_session()
    if session is None:
        return JSONResponse({'status': 'success', 'state': 'idle'})
    payload = {'status': 'success', 'state': session.state, 'error': session.error}
    if session.state == 'success' and session.result:
        payload['result'] = session.result
    return JSONResponse(payload)


async def login_shot(request: Request):
    """验证码区域最新截图，前端 <img> 高频直刷用"""
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    session = _current_session()
    shot = session.get_screenshot() if session is not None else b''
    if not shot:
        return Response(status_code=204)
    return Response(content=shot, media_type='image/png', headers={'Cache-Control': 'no-store'})


async def login_drag(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
        data = await request.json()
        phase = data['phase']
        x = float(data['x'])
        y = float(data['y'])
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    except (KeyError, ValueError, TypeError):
        return JSONResponse({'status': 'error', 'message': 'Expected phase, x, y fields.'}, status_code=400)
    if phase not in ('start', 'move', 'end'):
        return JSONResponse({'status': 'error', 'message': 'Invalid phase.'}, status_code=400)
    session = _current_session()
    if session is None or session.state != 'captcha':
        return JSONResponse({'status': 'error', 'message': 'No active captcha.'}, status_code=409)
    session.submit_drag_event(phase, x, y)
    return JSONResponse({'status': 'success'})


async def login_cancel(request: Request):
    name = request.path_params['name']
    try:
        validate_instance(name)
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    session = _current_session()
    if session is not None:
        session.cancel()
    return JSONResponse({'status': 'success'})


async def bind_to_bot(request: Request):
    """把当前实例的 Cookie 直传给 AstrBot 插件，完成该链接所属 QQ 的账号绑定。"""
    name = request.path_params['name']
    try:
        validate_instance(name)
        data = await request.json()
        link = str(data.get('link', '')).strip()
    except InstanceNotFound as exc:
        return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=404)
    except (KeyError, ValueError, TypeError):
        return JSONResponse({'status': 'error', 'message': 'Expected link field.'}, status_code=400)

    parsed = urlsplit(link)
    matched = BIND_PATH_RE.match(parsed.path)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or matched is None:
        return JSONResponse(
            {'status': 'error', 'message': '绑定链接格式不正确，请粘贴机器人私聊返回的完整链接。'},
            status_code=400,
        )

    config = State.config_updater.read_file(name)
    cookie = str(deep_get(config, 'BlaAuth.BlaAuth.Cookie', '') or '')
    x_common_params = str(deep_get(config, 'BlaAuth.BlaAuth.XCommonParams', '') or '')
    user_agent = str(deep_get(config, 'BlaAuth.BlaAuth.UserAgent', '') or '')
    if not cookie or not x_common_params:
        return JSONResponse(
            {'status': 'error', 'message': '当前实例还没有 Cookie，请先点击「一键登录」获取。'},
            status_code=400,
        )

    # 插件侧按每条 Cookie 的 domain 过滤，缺失 domain 的条目会被直接丢弃
    cookies = []
    for part in cookie.split(';'):
        entry = part.strip()
        if '=' not in entry:
            continue
        key, value = (item.strip() for item in entry.split('=', 1))
        if key and value:
            cookies.append({'name': key, 'value': value, 'domain': '.blablalink.com'})

    present = {item['name'] for item in cookies}
    missing = [key for key in REQUIRED_BIND_COOKIES if key not in present]
    if missing:
        return JSONResponse(
            {'status': 'error', 'message': '缺少必要 Cookie：' + '、'.join(missing) + '，请重新「一键登录」。'},
            status_code=400,
        )

    # 站点在网页登录场景下发出的 xcp 不含顶层 openid（仅游戏内场景才带），
    # 插件侧 submit_cookies 要求它非空；按插件扩展的兜底口径补上 cookie 里的 game_openid
    try:
        xcp_data = json.loads(x_common_params)
    except ValueError:
        xcp_data = None
    if isinstance(xcp_data, dict) and not str(xcp_data.get('openid', '')).strip():
        game_openid = next((item['value'] for item in cookies if item['name'] == 'game_openid'), '')
        if game_openid:
            xcp_data['openid'] = game_openid
            x_common_params = json.dumps(xcp_data, ensure_ascii=False, separators=(',', ':'))

    payload = {
        'token': matched.group(1),
        'cookies': cookies,
        'x_common_params': x_common_params,
        'user_agent': user_agent,
    }
    # 插件侧要真去 BlaBlaLink 验活，且失败会重试，超时留足余量
    endpoint = f'{parsed.scheme}://{parsed.netloc}/api/bind/cookies'
    try:
        async with httpx.AsyncClient(timeout=BIND_TIMEOUT) as client:
            response = await client.post(endpoint, json=payload)
    except httpx.HTTPError as exc:
        logger.warning(f'Bind to bot request failed for {name}: {exc}')
        return JSONResponse(
            {'status': 'error', 'message': '无法连接绑定服务，请确认链接中的地址可以访问。'},
            status_code=502,
        )

    try:
        body = response.json()
    except ValueError:
        return JSONResponse(
            {'status': 'error', 'message': f'绑定服务返回了非预期内容（HTTP {response.status_code}）。'},
            status_code=502,
        )

    if response.status_code == 200 and body.get('ok'):
        nickname = str(body.get('nickname') or '')
        qq_id = str(body.get('qq_id') or '')
        message = f'已绑定到 QQ {qq_id}' + (f'（{nickname}）' if nickname else '')
        logger.info(f'Bind to bot succeeded for {name}: {message}')
        return JSONResponse({'status': 'success', 'message': message})

    message = str(body.get('error') or body.get('message') or '绑定失败，请重试。')
    logger.warning(f'Bind to bot rejected for {name}: HTTP {response.status_code} {message}')
    return JSONResponse({'status': 'error', 'message': message}, status_code=400)
