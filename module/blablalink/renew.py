"""
blablalink Cookie 自动续期

运行机制：
1. Cookie 里的 game_token 有效期约 30 天。临期或失效时，不必重新输账号密码登录：
   lipass（Level Infinite Pass）支持用仍在有效期内的 game_openid/game_token
   直接换新 token，有效期重新计算 30 天，理论可无限滚动续期。
2. 换 token 必须走官方 Web SDK（接口有 sig 签名，VM 混淆，无法纯 HTTP 重放）：
   用无头浏览器打开 blablalink 主页，注入 Pass SDK（index.umd.js），在页面内执行两步：
   refreshCAccTokenByOpenID（game_token 换 lipass token）
   → intlSignIn（lipass token 换新 game_token）。
3. Pass SDK 初始化需要的 env/gameID/appID 不写死：点开主页的 Sign In 触发站点
   登录 SDK 初始化，从 console 的 login_pop_config 抓站点运行时配置；
   gameID 备用来源是 cookie 的 game_gameid 与 XCommonParams 的 intl_game_id；
   channelID 从现有 cookie 的 game_channelid 取（与一键登录写入的值同源）。
   都取不到直接报错。
4. 浏览器按 系统 Chrome → 系统 Edge → 自动下载内置 Chromium 的回退链启动；
   playwright 缺失时自动 pip 安装（尊重 deploy.yaml 的 PypiMirror）。
5. 返回码约定：refresh 阶段 ret=11002 表示 token 已彻底失效，只能重新登录
   （走 login.py 的一键登录）；signin 阶段 ret=808099001 表示缺 account 字段，
   调用方需传入 LiPass 账号邮箱。
"""
import json
import subprocess
import sys
from typing import Optional, Tuple

from module.logger import logger

# Level Infinite Pass（lipass）Web SDK 地址与站点入口
LIPASS_SDK_URL = 'https://common-web.intlgame.com/sdk-cdn/infinite-pass/latest/index.umd.js'
BLA_HOME = 'https://www.blablalink.com/'
# 主页登录入口按钮（触发登录 SDK 初始化，从而暴露 login_pop_config）
SEL_SIGN_IN = 'button:has-text("Sign In")'
# OneTrust cookie 提示的接受按钮，不点掉会挡住 Sign In
SEL_COOKIE_ACCEPT = '#onetrust-accept-btn-handler'

# refresh_sacc_token 返回码：token 已过期/失效，只能人工重新登录
RET_TOKEN_INVALID = 11002


class RenewError(Exception):
    pass


def _read_pypi_mirror() -> Optional[str]:
    """读取 deploy.yaml 里的 pip 镜像配置"""
    try:
        from deploy.utils import DEPLOY_CONFIG, poor_yaml_read
        # poor_yaml_read 会把嵌套 yaml 打平，PypiMirror 在顶层
        deploy = poor_yaml_read(DEPLOY_CONFIG)
        mirror = deploy.get('PypiMirror')
        if mirror and isinstance(mirror, str) and mirror.lower() != 'null':
            return mirror
    except Exception as e:
        logger.warning(f'Failed to read deploy config for pip mirror: {e}')
    return None


def _run_pip(args: list) -> bool:
    """执行 pip/playwright 安装命令，返回是否成功"""
    cmd = [sys.executable, '-m'] + args
    mirror = _read_pypi_mirror()
    if mirror and args[0] == 'pip':
        cmd += ['-i', mirror]
        from urllib.parse import urlparse
        host = urlparse(mirror).hostname
        if host:
            cmd += ['--trusted-host', host]
    logger.info(f'Running: {" ".join(cmd)}')
    try:
        proc = subprocess.run(cmd, timeout=1800)
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error('Install command timed out')
        return False
    except Exception as e:
        logger.error(f'Install command failed: {e}')
        return False


def ensure_playwright():
    """确保 playwright 可用，缺失时自动 pip 安装"""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        logger.warning('playwright not installed, installing automatically...')
        logger.info('This is a one-time setup for Blablalink auto renew (~35MB pip package)')
        if not _run_pip(['pip', 'install', '--disable-pip-version-check', 'playwright']):
            raise RenewError('Failed to install playwright, please run: pip install playwright')
        try:
            from playwright.sync_api import sync_playwright
            return sync_playwright
        except ImportError as e:
            raise RenewError(f'playwright still not importable after install: {e}')


def _launch_browser(p):
    """按回退链启动无头浏览器：系统 Chrome → 系统 Edge → 下载内置 Chromium"""
    for channel in ('chrome', 'msedge'):
        try:
            browser = p.chromium.launch(headless=True, channel=channel)
            logger.info(f'Launched system browser via channel: {channel}')
            return browser
        except Exception as e:
            logger.info(f'Channel {channel} unavailable: {str(e)[:150]}')

    # 系统浏览器不可用，尝试内置 Chromium，缺失则自动下载
    for attempt in range(3):
        try:
            browser = p.chromium.launch(headless=True)
            logger.info('Launched bundled chromium')
            return browser
        except Exception as e:
            msg = str(e)
            logger.warning(f'Launch chromium failed (attempt {attempt + 1}): {msg[:200]}')
            if attempt == 0:
                logger.info('Downloading chromium (~170MB), one-time setup...')
                if not _run_pip(['playwright', 'install', 'chromium']):
                    raise RenewError('Failed to install chromium browser')
            elif attempt == 1:
                # Linux/Docker 可能缺系统库（libnss3 等），尝试 --with-deps（需要 root）
                logger.info('Installing chromium system dependencies (requires root on Linux)...')
                if not _run_pip(['playwright', 'install', '--with-deps', 'chromium']):
                    raise RenewError(
                        'Failed to install chromium dependencies, '
                        'please run manually: playwright install --with-deps chromium'
                    )
            else:
                raise RenewError('Failed to launch any browser for token renewal')
    raise RenewError('Failed to launch browser')


def _parse_cookie(cookie: str) -> dict:
    result = {}
    for pair in cookie.split(';'):
        pair = pair.strip()
        if '=' in pair:
            k, _, v = pair.partition('=')
            result[k.strip()] = v.strip()
    return result


def _build_cookie(old: dict, openid: str, token: str) -> str:
    """保留旧 cookie 的其他字段，只替换 game_openid / game_token"""
    data = dict(old)
    data['game_openid'] = openid
    data['game_token'] = token
    return '; '.join(f'{k}={v}' for k, v in data.items())


# 页面内执行的两步续期脚本：refresh_sacc_token 换 lipass token，再 intlSignIn 换新 game_token
_RENEW_JS = """
async ({ gameOpenid, gameToken, account }) => {
    const inst = new window.PassFactory.Pass({
        env: 'ENV_PLACEHOLDER', gameID: 'GAME_ID_PLACEHOLDER', appID: 'APP_ID_PLACEHOLDER',
        accountPlatType: CHANNEL_ID_PLACEHOLDER, langType: 'en', renderMode: 'inline',
    });
    const client = await inst.getAuthClient();
    const refresh = await client.refreshCAccTokenByOpenID({
        openid: gameOpenid, token: gameToken,
        oauth_channelid: CHANNEL_ID_PLACEHOLDER, channel_id: CHANNEL_ID_PLACEHOLDER,
    });
    if (!refresh || refresh.ret !== 0) {
        return { stage: 'refresh', ret: refresh && refresh.ret, msg: refresh && refresh.msg };
    }
    const login = await client.intlSignIn({
        openid: String(refresh.uid), token: refresh.token,
        account_plat_type: CHANNEL_ID_PLACEHOLDER, account: account || '',
        channel_id: CHANNEL_ID_PLACEHOLDER,
    });
    if (!login || !login.token || !login.openid) {
        return { stage: 'signin', ret: login && login.ret, msg: login && login.msg };
    }
    return {
        stage: 'ok', ret: 0,
        openid: String(login.openid), token: login.token,
        expire: login.token_expire_time || 0,
    };
}
"""


def renew_cookie(cookie: str, account: str = '', user_agent: str = '', game_id: str = '') -> Optional[Tuple[str, int]]:
    """
    用 cookie 中仍在有效期内的 game_openid/game_token 续期，换取新 game_token（新 30 天有效期）。

    Args:
        cookie: 现有的 BlaAuth cookie 字符串
        account: LiPass 账号邮箱（intlSignIn 的 channel_info.account，可空）
        user_agent: 浏览器 UA
        game_id: gameID 备用来源（XCommonParams 的 intl_game_id），
            优先取站点 login_pop_config 与 cookie 的 game_gameid

    Returns:
        (新 cookie 字符串, 过期时间戳)；续期失败返回 None
    """
    creds = _parse_cookie(cookie)
    game_openid = creds.get('game_openid', '')
    game_token = creds.get('game_token', '')
    if not game_openid or not game_token:
        logger.error('Cookie missing game_openid or game_token, cannot renew')
        return None
    # channelID 不写死：取现有 cookie 的 game_channelid（一键登录写入的值）
    channel_id = creds.get('game_channelid', '')
    if not channel_id:
        logger.error('channel_id not found in cookie (game_channelid), cannot renew')
        return None

    sync_playwright = ensure_playwright()

    with sync_playwright() as p:
        browser = _launch_browser(p)
        try:
            context_args = {}
            if user_agent:
                context_args['user_agent'] = user_agent
            context = browser.new_context(**context_args)
            page = context.new_page()

            # env/gameID/appID 不写死：点 Sign In 触发站点登录 SDK 初始化，
            # 从 console 的 login_pop_config 抓站点运行时配置
            sdk_conf = {}

            def on_console(msg):
                if msg.text.startswith('[login] login_pop_config'):
                    try:
                        sdk_conf.update(json.loads(msg.text.split(' ', 2)[2]))
                    except Exception:
                        pass

            page.on('console', on_console)
            page.goto(BLA_HOME, wait_until='domcontentloaded')
            page.wait_for_timeout(5000)
            try:
                page.click(SEL_COOKIE_ACCEPT, timeout=3000)
            except Exception:
                pass
            try:
                page.click(SEL_SIGN_IN, timeout=10000)
            except Exception as e:
                logger.warning(f'Click Sign In failed: {str(e)[:100]}')
            for _ in range(10):
                if sdk_conf:
                    break
                page.wait_for_timeout(1000)
            env = str(sdk_conf.get('env', ''))
            app_id = str(sdk_conf.get('appID', ''))
            # gameID 优先站点运行时配置，其次 cookie / XCommonParams
            game_id_final = str(sdk_conf.get('gameID', '')) or creds.get('game_gameid', '') or game_id
            if not env or not app_id:
                logger.error(f'login_pop_config not captured (env={env!r}, appID={app_id!r}), cannot renew')
                return None
            if not game_id_final:
                logger.error('game_id not found in login_pop_config, cookie or XCommonParams, cannot renew')
                return None
            logger.info(f'Pass SDK config: env={env}, gameID={game_id_final}, appID={app_id[:8]}..., channel={channel_id}')

            js = (_RENEW_JS
                  .replace('ENV_PLACEHOLDER', env)
                  .replace('GAME_ID_PLACEHOLDER', game_id_final)
                  .replace('APP_ID_PLACEHOLDER', app_id)
                  .replace('CHANNEL_ID_PLACEHOLDER', channel_id))

            page.add_script_tag(url=LIPASS_SDK_URL)
            page.wait_for_function('window.PassFactory !== undefined', timeout=15000)

            logger.info('Refreshing lipass token...')
            result = page.evaluate(js, {
                'gameOpenid': game_openid,
                'gameToken': game_token,
                'account': account,
            })
        finally:
            browser.close()

    if not isinstance(result, dict):
        logger.error(f'Renew returned unexpected result: {result}')
        return None
    if result.get('stage') != 'ok':
        ret = result.get('ret')
        msg = result.get('msg')
        if result.get('stage') == 'refresh' and ret == RET_TOKEN_INVALID:
            logger.error(f'Token already expired, manual login required: ret={ret}, msg={msg}')
        else:
            logger.error(f'Renew failed at {result.get("stage")}: ret={ret}, msg={msg}')
        return None

    new_cookie = _build_cookie(creds, result['openid'], result['token'])
    expire = int(result.get('expire') or 0)
    logger.info(f'Token renewed successfully, new expire: {expire}')
    return new_cookie, expire
