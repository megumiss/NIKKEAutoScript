import json
import subprocess
import sys
from typing import Optional, Tuple

from module.logger import logger

# Level Infinite Pass（lipass）Web SDK 常量，来自 blablalink 登录链路实测
LIPASS_SDK_URL = 'https://common-web.intlgame.com/sdk-cdn/infinite-pass/latest/index.umd.js'
LIPASS_ENV = 'aws-na'
LIPASS_APP_ID = '09af79d65d6e4fdf2d2569f0d365739d'
BLA_HOME = 'https://www.blablalink.com/'
GAME_ID = '29080'
CHANNEL_ID = 131

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


def renew_cookie(cookie: str, account: str = '', user_agent: str = '') -> Optional[Tuple[str, int]]:
    """
    用 cookie 中仍在有效期内的 game_openid/game_token 续期，换取新 game_token（新 30 天有效期）。

    Args:
        cookie: 现有的 BlaAuth cookie 字符串
        account: LiPass 账号邮箱（intlSignIn 的 channel_info.account，可空）
        user_agent: 浏览器 UA

    Returns:
        (新 cookie 字符串, 过期时间戳)；续期失败返回 None
    """
    creds = _parse_cookie(cookie)
    game_openid = creds.get('game_openid', '')
    game_token = creds.get('game_token', '')
    if not game_openid or not game_token:
        logger.error('Cookie missing game_openid or game_token, cannot renew')
        return None

    sync_playwright = ensure_playwright()

    js = (_RENEW_JS
          .replace('ENV_PLACEHOLDER', LIPASS_ENV)
          .replace('GAME_ID_PLACEHOLDER', GAME_ID)
          .replace('APP_ID_PLACEHOLDER', LIPASS_APP_ID)
          .replace('CHANNEL_ID_PLACEHOLDER', str(CHANNEL_ID)))

    with sync_playwright() as p:
        browser = _launch_browser(p)
        try:
            context_args = {}
            if user_agent:
                context_args['user_agent'] = user_agent
            context = browser.new_context(**context_args)
            page = context.new_page()
            page.goto(BLA_HOME, wait_until='domcontentloaded')
            page.wait_for_timeout(3000)
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
