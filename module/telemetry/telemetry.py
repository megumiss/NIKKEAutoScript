import datetime
import json
import subprocess
import sys
import threading
import uuid

import requests

from deploy.utils import DEPLOY_CONFIG, poor_yaml_read
from module.config.utils import deep_get
from module.logger import logger

REPORT_URL = 'https://nkas.megumiss.top/dau-77kk/report'
REPORT_TIMEOUT = 5
# 放在 ./data 而不是 ./config：nkas_instance() 会把 config 下每个 *.json 识别成实例
STATE_FILE = './data/telemetry.json'


def _load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f)
    except OSError as e:
        logger.debug(f'Telemetry state save failed: {e}')


def _get_client_id(state):
    client_id = state.get('id')
    if isinstance(client_id, str) and client_id:
        return client_id
    return str(uuid.uuid4())


def _get_os_label():
    if sys.platform == 'win32':
        try:
            build = sys.getwindowsversion().build
        except (AttributeError, OSError):
            return 'Windows 10'
        return 'Windows 11' if build >= 22000 else 'Windows 10'
    if sys.platform == 'darwin':
        return 'macOS'
    if sys.platform.startswith('linux'):
        return 'Linux'
    return None


def _get_version():
    git = poor_yaml_read(DEPLOY_CONFIG).get('GitExecutable') or 'git'
    for exe in (git, 'git'):
        try:
            return subprocess.check_output(
                [exe, 'rev-parse', '--short', 'HEAD'],
                stderr=subprocess.DEVNULL,
                timeout=REPORT_TIMEOUT,
                text=True,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return 'unknown'


def _get_resolution(config_name):
    """
    仅 PC 客户端上报屏幕分辨率；开启多屏幕模式时使用目标屏幕，否则使用主屏幕
    """
    try:
        with open(f'./config/{config_name}.json', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if deep_get(data, 'Client.Platform', default='win') != 'win':
        return None

    screen_n = 0
    if deep_get(data, 'PCClient.Screens', default=False):
        screen_n = deep_get(data, 'PCClient.ScreenNumber', default=0)
    try:
        from desktopmagic.screengrab_win32 import getDisplayRects

        rects = getDisplayRects()
    except Exception as e:
        logger.debug(f'Telemetry get display rects failed: {e}')
        return None
    if not rects:
        return None
    if not isinstance(screen_n, int) or not 0 <= screen_n < len(rects):
        screen_n = 0
    left, top, right, bottom = rects[screen_n]
    if right <= left or bottom <= top:
        return None
    return f'{right - left}x{bottom - top}'


def _send(payload):
    try:
        resp = requests.post(REPORT_URL, json=payload, timeout=REPORT_TIMEOUT)
        if 200 <= resp.status_code < 300:
            logger.info('Usage statistics reported')
            return True
        logger.debug(f'Telemetry report failed, code {resp.status_code}')
    except requests.RequestException as e:
        logger.debug(f'Telemetry report failed: {e}')
    return False


def _run(config_name, today, state):
    """
    后台线程：采集字段并上报，成功后才记录 last_report（失败留待下次启动重试）
    """
    try:
        state['id'] = _get_client_id(state)
        payload = {'id': state['id'], 'version': _get_version()}
        os_label = _get_os_label()
        if os_label:
            payload['os'] = os_label
        resolution = _get_resolution(config_name)
        if resolution:
            payload['res'] = resolution

        if _send(payload):
            state['last_report'] = today
        _save_state(state)
    except Exception as e:
        logger.debug(f'Telemetry report failed: {e}')


def report_statistics(config_name='nkas'):
    """
    启动时上报一次匿名使用统计，每天最多一次。
    主线程只做开关与当日去重检查，采集和上报都在后台线程执行，
    任何失败都不影响主流程。
    可在 config/deploy.yaml 中设置 EnableStatistics: false 关闭。
    """
    try:
        enabled = poor_yaml_read(DEPLOY_CONFIG).get('EnableStatistics', True)
        if enabled is not True:
            logger.debug('Telemetry disabled by EnableStatistics')
            return

        state = _load_state()
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        if state.get('last_report') == today:
            return

        threading.Thread(
            target=_run, args=(config_name, today, state), daemon=True
        ).start()
    except Exception as e:
        logger.debug(f'Telemetry init failed: {e}')
