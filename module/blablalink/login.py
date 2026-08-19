"""
blablalink 一键登录获取 Cookie

运行机制：
1. 纯接口登录不可行（lipass 登录接口有 sig 签名，VM 混淆；且腾讯验证码有
   machine check），所以用无头浏览器驱动真实登录页完成登录。
   浏览器按 系统 Chrome → 系统 Edge → 自动下载内置 Chromium 的回退链启动。
2. 页面流程：接受 OneTrust cookie 提示 → Sign In → 港澳台账号（按实例客户端
   设置：PC 端 PCClient.PCClientInfo.Client / 模拟器 Emulator.PackageName）
   在登录弹窗的区域下拉框点选 HK/MC/TW，首次登录没有 XCommonParams 也有区服
   依据，切换后登录 SDK 重新初始化（login_pop_config 重新打印）→ 填账号密码 →
   Log in。腾讯验证码先走无感校验（可能直接通过）；返回 ret=2170 时站点 SDK
   会弹出交互滑块（腾讯验证码 iframe 不能搬离官方域名渲染，会按 entry_url
   域名校验 403），此时把验证码区域截图推到 Web UI 弹窗，用户在其中拖动，
   鼠标事件实时转发回页面完成验证。
3. 登录成功后从 localStorage 的 logined_account_cache_key（base64 JSON）取
   openid/token/token_expire_time/channel_info.channelId；
   gameID 取自登录 SDK 初始化时打印的 login_pop_config（console 监听）。
   所有值均来自站点运行时，不写死；取不到直接报错。
4. 站点的 x-common-params 拦截器按 URL ?gameid= → 上下文 cookie → localStorage
   解析游戏上下文；真实浏览器的该 cookie 由站点在此前访问中种好（365 天），
   全新无头上下文里站点自己不会设置（实测点头像也不会），导致请求头里
   game_id/area_id/intl_game_id 全空。因此登录后按站点自身机制补种
   __ss_storage_cookie_cache_game_id__ / __ss_storage_cookie_cache_lang__
   （先按名清旧值，避免域差异产生重复 cookie）。
5. 进入个人主页一次：站点此时发出的请求自带完整 x-common-params（抓取时按
   非空字段数记分，只留最完整的一份），同时 GetUserProfile 提供用户名。
6. 用运行时值构造最小业务 cookie（game_openid/game_channelid/game_token/
   game_gameid 等），调 CheckLogin 验证有效后写回配置
   （Cookie/XCommonParams/LoginUser/TokenExpire）。
"""
import base64
import json
import queue
import threading
import time
from typing import Optional

from module.blablalink.renew import (
    BLA_HOME,
    ELEMENT_TIMEOUT,
    GOTO_TIMEOUT,
    SEL_COOKIE_ACCEPT,
    SEL_SIGN_IN,
    WAIT_SHORT,
    _launch_browser,
    ensure_playwright,
    select_login_region,
)
from module.logger import logger

CHECK_LOGIN_URL = 'https://api.blablalink.com/api/user/CheckLogin'
# 登录成功后 localStorage 中的凭证缓存（base64 JSON，含 openid/token/token_expire_time）
LOGIN_CACHE_KEY = 'logined_account_cache_key'
# 验证码截图刷新间隔（秒）
SHOT_INTERVAL = 0.7
# 提交后等登录结果的时长（秒）：无感验证本身可能耗时 30s+，
# 2170 后 SDK 还要再加载交互验证码
LOGIN_WAIT_TIMEOUT = 120
# 滑块人机拖动阶段的默认时长（秒）
CAPTCHA_TIMEOUT = 180
# CheckLogin HTTP 请求超时（秒）
HTTP_TIMEOUT = 30

# 登录表单选择器（blablalink 当前页面结构，页面改版可能需要更新；
# SEL_COOKIE_ACCEPT / SEL_SIGN_IN 与续期共用，定义在 renew.py）
SEL_ACCOUNT = '#loginPwdForm_account'
SEL_PASSWORD = '#loginPwdForm_password'
SEL_SUBMIT = 'button:has-text("Log in")'


class LoginError(Exception):
    pass


class LoginCancelled(LoginError):
    pass


class LoginTimeout(LoginError):
    pass


class LoginVerifyError(LoginError):
    pass


class LoginFormError(LoginError):
    pass


def build_cookie(openid: str, token: str, game_id: str, channel_id: str, extra: Optional[dict] = None) -> str:
    """按站点 cookie 结构构造最小可用 cookie（实测业务接口只需这些字段）"""
    data = {
        'game_openid': openid,
        'game_channelid': channel_id,
        'game_token': token,
        'game_gameid': game_id,
        'game_login_game': '0',
        'game_adult_status': '1',
    }
    if extra:
        data.update(extra)
    return '; '.join(f'{k}={v}' for k, v in data.items())


def check_login_cookie(cookie: str, xcommonparams: str, user_agent: str, language: str = 'zh-TW') -> bool:
    """用抓回的 cookie 调 CheckLogin 验证有效性"""
    import requests
    headers = {
        'cookie': cookie,
        'x-common-params': xcommonparams,
        'x-language': language,
        'user-agent': user_agent,
    }
    try:
        resp = requests.get(CHECK_LOGIN_URL, headers=headers, timeout=HTTP_TIMEOUT)
        data = resp.json()
    except Exception as e:
        logger.error(f'CheckLogin request failed: {e}')
        return False
    return data.get('code') == 0 and data.get('msg') == 'ok'


def _xcp_score(xcp: str) -> int:
    """x-common-params 完整度记分：非空字段数；解析失败记 0"""
    try:
        data = json.loads(xcp)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    return sum(1 for v in data.values() if v)


def _xcp_empty_keys(xcp: str) -> list:
    """列出 x-common-params 中的空字段，用于判断登录态是否初始化完整"""
    try:
        data = json.loads(xcp)
    except Exception:
        return ['<parse failed>']
    if not isinstance(data, dict):
        return ['<not a dict>']
    return [k for k, v in data.items() if not v]


def _parse_username(data) -> str:
    """从 GetUserProfile 响应中提取用户名（data.info.username），做宽容搜索"""
    result = ['']

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in ('username', 'nickname', 'nick_name', 'user_name') and isinstance(v, str) and v and not result[0]:
                    result[0] = v
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return result[0]


class BlaLoginSession:
    """
    一次自动登录会话。run() 需在独立线程中执行（Playwright 同步 API 单线程约束），
    HTTP 层通过 submit_drag()/cancel()/get_screenshot() 与其交互。
    """

    def __init__(self, account: str, password: str, user_agent: str = '',
                 language: str = 'zh-TW', server: str = '', timeout: int = CAPTCHA_TIMEOUT):
        self.account = account
        self.password = password
        self.user_agent = user_agent
        self.language = language
        # NKAS 区服（intl/tw/hmt），港澳台需要在登录弹窗的区域下拉框切换
        self.server = server
        self.timeout = timeout

        self._lock = threading.Lock()
        self._state = 'idle'  # idle/launching/logging_in/captcha/success/cancelled/timeout/error
        self._error = ''
        self._shot = b''
        self._drag_queue = queue.Queue()
        self._cancel = threading.Event()
        self._login_ok = threading.Event()
        self.result = None  # {'cookie', 'xcommonparams', 'expire'}

    # ---------- HTTP 层交互接口（线程安全） ----------

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    def _set_state(self, state: str, error: str = ''):
        with self._lock:
            self._state = state
            self._error = error

    def get_screenshot(self) -> bytes:
        with self._lock:
            return self._shot

    def submit_drag_event(self, phase: str, x: float, y: float):
        """提交一个拖拽事件（phase: start/move/end，图片坐标系），实时转发到页面"""
        self._drag_queue.put((phase, x, y))

    def cancel(self):
        self._cancel.set()

    # ---------- 内部流程（仅 run 线程调用） ----------

    def _find_captcha_box(self, page):
        """找到视口内可见的验证码 iframe 区域，无则返回 None"""
        for frame in page.frames:
            url = frame.url
            if url in ('about:blank', '') or url.startswith(BLA_HOME):
                continue
            try:
                box = frame.frame_element().bounding_box()
            except Exception:
                continue
            if box and box['width'] > 100 and box['x'] >= 0 and box['y'] >= 0:
                return box
        return None

    def _update_shot(self, page):
        try:
            box = self._find_captcha_box(page)
            if box:
                shot = page.screenshot(clip=box)
                with self._lock:
                    self._shot = shot
        except Exception:
            pass

    def _handle_drag_event(self, page, ev, drag: dict):
        """处理一个拖拽事件，实时驱动页面鼠标，drag 持有跨事件状态"""
        phase, x, y = ev
        if phase == 'start':
            box = self._find_captcha_box(page)
            if not box:
                logger.warning('Drag start ignored: captcha box not found')
                return
            drag['box'] = box
            drag['active'] = True
            page.mouse.move(box['x'] + x, box['y'] + y)
            page.mouse.down()
        elif phase == 'move' and drag.get('active'):
            box = drag['box']
            page.mouse.move(box['x'] + x, box['y'] + y)
        elif phase == 'end' and drag.get('active'):
            box = drag['box']
            page.mouse.move(box['x'] + x, box['y'] + y)
            page.mouse.up()
            drag['active'] = False
            drag['box'] = None
            logger.info('Drag finished')

    def _read_login_cache(self, page) -> dict:
        """从 localStorage 读取登录凭证缓存并解码"""
        raw = page.evaluate(f"localStorage.getItem('{LOGIN_CACHE_KEY}')")
        if not raw:
            return {}
        try:
            raw = raw.strip().strip('"')
            raw += '=' * (-len(raw) % 4)
            return json.loads(base64.b64decode(raw).decode('utf-8', 'ignore'))
        except Exception as e:
            logger.error(f'Failed to decode login cache: {e}')
            return {}

    def run(self):
        self._set_state('launching')
        browser = None
        try:
            sync_playwright = ensure_playwright()
            with sync_playwright() as p:
                browser = _launch_browser(p)
                context_args = {}
                if self.user_agent:
                    context_args['user_agent'] = self.user_agent
                context = browser.new_context(**context_args)
                page = context.new_page()

                # 捕获站点真实请求中的 x-common-params 与用户信息（字段并非常量，以站点实际发出为准）
                # 只抓登录成功后的请求：未登录时站点也会发公开请求，其 page_id/language 不是登录态的值
                captured = {'xcommonparams': '', 'score': -1, 'profile': {}, 'game_id': ''}

                def on_request(req):
                    if not self._login_ok.is_set():
                        return
                    if 'api.blablalink.com' in req.url:
                        xcp = req.headers.get('x-common-params')
                        if not xcp:
                            return
                        # 登录态初始化需要时间，早期请求的 x-common-params 可能带空字段
                        # （如 game_id），按非空字段数记分，只保留最完整的一份；
                        # 同分时后到的覆盖：最后导航的个人主页请求 page_id 带 openid、
                        # language 是种入的配置语言，更贴近真实浏览器的登录态请求
                        score = _xcp_score(xcp)
                        if score >= captured['score']:
                            captured['xcommonparams'] = xcp
                            captured['score'] = score
                            logger.info(f'Captured x-common-params from {req.url[-60:]}, score {score}')

                def on_console(msg):
                    text = msg.text
                    # 登录 SDK 初始化时打印的配置，含本站 gameID（站点运行时真实值）
                    if text.startswith('[login] login_pop_config'):
                        try:
                            cfg = json.loads(text.split(' ', 2)[2])
                            captured['game_id'] = str(cfg.get('gameID', ''))
                        except Exception:
                            pass
                    if any(k in text.lower() for k in ('signin', 'captcha', 'login')):
                        logger.info(f'[page] {text[:150]}')

                def on_response(resp):
                    url = resp.url
                    if 'GetUserProfile' in url and 'api.blablalink.com' in url:
                        try:
                            captured['profile'] = resp.json()
                            logger.info(f'GetUserProfile response: {resp.text()[:400]}')
                        except Exception:
                            pass
                        return
                    if 'account/login' not in url:
                        return
                    try:
                        body = resp.text()
                    except Exception:
                        return
                    logger.info(f'account/login response: {body[:200]}')
                    if '"ret":0' in body.replace(' ', ''):
                        self._login_ok.set()

                page.on('request', on_request)
                page.on('response', on_response)
                page.on('console', on_console)

                page.goto(BLA_HOME, wait_until='domcontentloaded', timeout=GOTO_TIMEOUT)
                page.wait_for_timeout(WAIT_SHORT)
                if self._cancel.is_set():
                    raise LoginCancelled

                # 自动填表提交
                self._set_state('logging_in')
                try:
                    try:
                        page.click(SEL_COOKIE_ACCEPT, timeout=ELEMENT_TIMEOUT)
                    except Exception:
                        pass
                    page.click(SEL_SIGN_IN, timeout=ELEMENT_TIMEOUT)
                    page.wait_for_timeout(WAIT_SHORT)
                    # 港澳台账号需在登录弹窗切换区域（选不上会抛错，不会按错误的国际服继续），
                    # 切换后登录 SDK 重新初始化并重新打印 login_pop_config，on_console 覆盖为最新值
                    select_login_region(page, self.server)
                    page.wait_for_selector(SEL_ACCOUNT, timeout=ELEMENT_TIMEOUT)
                    page.fill(SEL_ACCOUNT, self.account)
                    page.fill(SEL_PASSWORD, self.password)
                    page.click(SEL_SUBMIT, timeout=ELEMENT_TIMEOUT)
                except LoginCancelled:
                    raise
                except Exception as e:
                    raise LoginFormError(f'Login form automation failed: {str(e)[:200]}')
                logger.info('Login form submitted')

                # 等待：无感验证直接成功，或出现交互滑块
                deadline = time.time() + LOGIN_WAIT_TIMEOUT
                box = None
                while time.time() < deadline:
                    self._raise_if_cancelled()
                    if self._login_ok.is_set():
                        break
                    box = self._find_captcha_box(page)
                    if box:
                        break
                    # 轻量 driver 调用，保持事件泵运转
                    try:
                        page.evaluate('1')
                    except Exception:
                        pass
                    time.sleep(1)

                # 滑块阶段：截图推送 + 轨迹回放
                if not self._login_ok.is_set():
                    if not box:
                        # 超时前留档：当前 frames 与页面可见文本，便于诊断
                        try:
                            logger.warning(f'Login wait timeout, frames: {[f.url[:100] for f in page.frames]}')
                            text = page.evaluate("document.body ? document.body.innerText.slice(0, 500) : ''")
                            logger.warning(f'Page text: {text}')
                            page.screenshot(path='log/bla_login_timeout.png')
                        except Exception:
                            pass
                        raise LoginTimeout(f'No captcha and no login response in {LOGIN_WAIT_TIMEOUT}s')
                    self._set_state('captcha')
                    logger.info('Captcha shown, waiting for user drag via Web UI')
                    drag = {'active': False, 'box': None}
                    next_shot = 0
                    deadline = time.time() + self.timeout
                    while time.time() < deadline:
                        self._raise_if_cancelled()
                        if self._login_ok.is_set():
                            break
                        try:
                            ev = self._drag_queue.get(timeout=0.05)
                        except queue.Empty:
                            ev = None
                        if ev:
                            self._handle_drag_event(page, ev, drag)
                            next_shot = 0
                        now = time.time()
                        # 拖动期间提高截图频率，让画面跟手
                        interval = 0.1 if drag['active'] else SHOT_INTERVAL
                        if now >= next_shot:
                            self._update_shot(page)
                            next_shot = now + interval
                    else:
                        raise LoginTimeout
                    if not self._login_ok.is_set():
                        raise LoginTimeout

                # 登录成功：从 localStorage 取凭证
                logger.info('Login succeeded, reading credentials from localStorage')
                creds = {}
                for _ in range(5):
                    self._raise_if_cancelled()
                    creds = self._read_login_cache(page)
                    if creds.get('openid') and creds.get('token'):
                        break
                    page.wait_for_timeout(WAIT_SHORT)
                openid = str(creds.get('openid', ''))
                token = creds.get('token', '')
                if not openid or not token:
                    raise LoginVerifyError('Login succeeded but credentials not found in localStorage')
                expire = int(creds.get('token_expire_time') or 0)

                # 附带站点已有 cookie（如 OptanonConsent）
                extra = {}
                try:
                    for c in context.cookies(BLA_HOME):
                        if c['name'] == 'OptanonConsent':
                            extra['OptanonConsent'] = c['value']
                except Exception:
                    pass

                # 游戏 ID 与渠道 ID 必须来自站点运行时数据：
                # gameID 取自登录 SDK 初始化时的 login_pop_config，
                # channelId 取自 localStorage 凭证缓存的 channel_info；
                # 取不到说明页面结构/日志变了，直接报错，不使用任何固定值
                intl_game_id = captured['game_id']
                if not intl_game_id:
                    raise LoginVerifyError('gameID not found in login_pop_config')
                channel_id = str(creds.get('channel_info', {}).get('channelId', ''))
                if not channel_id:
                    raise LoginVerifyError('channelId not found in login cache (channel_info)')

                cookie = build_cookie(openid, token, intl_game_id, channel_id, extra)

                # 登录后诊断：上下文现有 cookie
                try:
                    keys = sorted(c['name'] for c in context.cookies(BLA_HOME))
                    logger.info(f'Context cookies after login: {keys}')
                except Exception:
                    pass

                # 登录后可能弹出 Additional Information 对话框，不关掉会挡住后续操作
                try:
                    page.click('button:has-text("Done")', timeout=ELEMENT_TIMEOUT)
                    logger.info('Dismissed additional information dialog')
                except Exception:
                    pass

                # 站点拦截器按 URL ?gameid= → cookie __ss_storage_cookie_cache_game_id__ → localStorage
                # 的顺序解析游戏上下文；真实浏览器里该 cookie 由站点在此前的访问中种好（365 天），
                # 全新无头上下文里站点任何交互都不会设置它（实测点头像也不行），
                # 导致 game_id/area_id/intl_game_id 全空。这里按站点自身机制补齐上下文，
                # 再让站点重新发出完整的 x-common-params。
                # 先按名清掉站点已种的旧值（域不一致时直接 add 会产生重复 cookie，读不到新值）
                try:
                    context.clear_cookies(name='__ss_storage_cookie_cache_game_id__')
                    context.clear_cookies(name='__ss_storage_cookie_cache_lang__')
                    context.add_cookies([
                        {'name': '__ss_storage_cookie_cache_game_id__', 'value': intl_game_id,
                         'domain': 'www.blablalink.com', 'path': '/'},
                        {'name': '__ss_storage_cookie_cache_lang__', 'value': self.language,
                         'domain': 'www.blablalink.com', 'path': '/'},
                    ])
                except Exception as e:
                    logger.warning(f'Plant context cookies failed: {str(e)[:100]}')

                # 进入自己的主页：站点以登录态发出带完整参数的 x-common-params，
                # 同时触发 GetUserProfile（取用户名，用于「当前登录用户」展示）
                openid_b64 = base64.b64encode(f'{intl_game_id}-{openid}'.encode()).decode()
                user_url = f'https://www.blablalink.com/user?openid={openid_b64}'
                try:
                    page.goto(user_url, wait_until='domcontentloaded', timeout=GOTO_TIMEOUT)
                except Exception as e:
                    logger.warning(f'Open profile page failed: {str(e)[:100]}')
                for _ in range(20):
                    self._raise_if_cancelled()
                    if captured['xcommonparams'] and not _xcp_empty_keys(captured['xcommonparams']) \
                            and captured['profile']:
                        break
                    page.wait_for_timeout(1000)

                xcommonparams = captured['xcommonparams']
                if not xcommonparams:
                    # 留档：页面 img 清单与截图，便于调整触发方式
                    try:
                        imgs = page.evaluate("[...document.querySelectorAll('img')].map(e => (e.src || '').slice(-60))")
                        logger.warning(f'Page imgs: {imgs}')
                        page.screenshot(path='log/bla_login_capture_fail.png')
                    except Exception:
                        pass
                    raise LoginVerifyError('Failed to capture x-common-params from site requests')

                empty_keys = _xcp_empty_keys(xcommonparams)
                if empty_keys:
                    raise LoginVerifyError(f'x-common-params incomplete, empty fields: {empty_keys}')

                # 从 GetUserProfile 响应提取用户名，用于「当前登录用户」展示
                username = _parse_username(captured['profile'])

                if not check_login_cookie(cookie, xcommonparams, self.user_agent, self.language):
                    raise LoginVerifyError('CheckLogin failed with captured cookie')
                self.result = {
                    'cookie': cookie,
                    'xcommonparams': xcommonparams,
                    'expire': expire,
                    'username': username,
                }
                self._set_state('success')
                logger.info(f'Login capture verified, user: {username}, expire: {expire}')
        except LoginCancelled:
            self._set_state('cancelled')
            logger.info('Login session cancelled by user')
        except LoginTimeout as e:
            self._set_state('timeout', str(e) or 'timeout')
            logger.warning(f'Login session timeout: {e}')
        except LoginError as e:
            self._set_state('error', str(e))
            logger.error(f'Login session failed: {e}')
        except Exception as e:
            self._set_state('error', str(e)[:300])
            logger.error(f'Login session exception: {e}')
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    def _raise_if_cancelled(self):
        if self._cancel.is_set():
            raise LoginCancelled
