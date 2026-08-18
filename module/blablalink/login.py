import base64
import json
import queue
import threading
import time
from typing import Optional

from module.blablalink.renew import BLA_HOME, _launch_browser, ensure_playwright
from module.logger import logger

CHECK_LOGIN_URL = 'https://api.blablalink.com/api/user/CheckLogin'
# 登录成功后 localStorage 中的凭证缓存（base64 JSON，含 openid/token/token_expire_time）
LOGIN_CACHE_KEY = 'logined_account_cache_key'
# 验证码截图刷新间隔（秒）
SHOT_INTERVAL = 0.7

# 登录表单选择器（blablalink 当前页面结构，页面改版可能需要更新）
SEL_COOKIE_ACCEPT = '#onetrust-accept-btn-handler'
SEL_SIGN_IN = 'button:has-text("Sign In")'
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


def build_cookie(openid: str, token: str, extra: Optional[dict] = None) -> str:
    """按站点 cookie 结构构造最小可用 cookie（实测业务接口只需这些字段）"""
    data = {
        'game_openid': openid,
        'game_channelid': '131',
        'game_token': token,
        'game_gameid': '29080',
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
        resp = requests.get(CHECK_LOGIN_URL, headers=headers, timeout=15)
        data = resp.json()
    except Exception as e:
        logger.error(f'CheckLogin request failed: {e}')
        return False
    return data.get('code') == 0 and data.get('msg') == 'ok'


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
                 language: str = 'zh-TW', timeout: int = 300):
        self.account = account
        self.password = password
        self.user_agent = user_agent
        self.language = language
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
                captured = {'xcommonparams': '', 'profile': {}}

                def on_request(req):
                    if not self._login_ok.is_set():
                        return
                    if 'api.blablalink.com' in req.url:
                        xcp = req.headers.get('x-common-params')
                        if xcp:
                            captured['xcommonparams'] = xcp
                            logger.info(f'Captured x-common-params from {req.url[-60:]}')

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
                page.on('console', lambda msg: logger.info(f'[page] {msg.text[:150]}')
                          if any(k in msg.text.lower() for k in ('signin', 'captcha', 'login')) else None)

                page.goto(BLA_HOME, wait_until='domcontentloaded')
                page.wait_for_timeout(5000)
                if self._cancel.is_set():
                    raise LoginCancelled

                # 自动填表提交
                self._set_state('logging_in')
                try:
                    try:
                        page.click(SEL_COOKIE_ACCEPT, timeout=3000)
                    except Exception:
                        pass
                    page.click(SEL_SIGN_IN, timeout=5000)
                    page.wait_for_selector(SEL_ACCOUNT, timeout=10000)
                    page.fill(SEL_ACCOUNT, self.account)
                    page.fill(SEL_PASSWORD, self.password)
                    page.click(SEL_SUBMIT, timeout=5000)
                except LoginCancelled:
                    raise
                except Exception as e:
                    raise LoginFormError(f'Login form automation failed: {str(e)[:200]}')
                logger.info('Login form submitted')

                # 等待：无感验证直接成功，或出现交互滑块
                # 无感验证本身可能耗时 30s+，2170 后 SDK 还要再加载交互验证码，给足余量
                deadline = time.time() + 150
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
                        raise LoginTimeout('No captcha and no login response in 150s')
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
                for _ in range(10):
                    self._raise_if_cancelled()
                    creds = self._read_login_cache(page)
                    if creds.get('openid') and creds.get('token'):
                        break
                    page.wait_for_timeout(2000)
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

                cookie = build_cookie(openid, token, extra)

                # 登录后诊断：上下文现有 cookie
                try:
                    keys = sorted(c['name'] for c in context.cookies(BLA_HOME))
                    logger.info(f'Context cookies after login: {keys}')
                except Exception:
                    pass

                # 登录后可能弹出 Additional Information 对话框，不关掉会挡住后续操作
                try:
                    page.click('button:has-text("Done")', timeout=3000)
                    logger.info('Dismissed additional information dialog')
                except Exception:
                    pass

                # 直接进入自己的主页，让站点以登录态发出真实请求：
                # 既触发 GetUserProfile（取用户名/uid），又能拿到 page_id 带 openid 的 x-common-params
                openid_b64 = base64.b64encode(f'29080-{openid}'.encode()).decode()
                try:
                    page.goto(f'https://www.blablalink.com/user?openid={openid_b64}', wait_until='domcontentloaded')
                except Exception as e:
                    logger.warning(f'Open profile page failed: {str(e)[:100]}')
                for _ in range(15):
                    self._raise_if_cancelled()
                    if captured['xcommonparams'] and captured['profile']:
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
