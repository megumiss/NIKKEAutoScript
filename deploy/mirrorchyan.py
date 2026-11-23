import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from deploy.config import ExecutionError

API_ROOT = 'https://mirrorchyan.com/api/resources'
SOURCE_RID = 'NIKKEAutoScript-Source'
DESKTOP_RID = 'NIKKEAutoScript'
RESOURCE_RID = 'NIKKEAutoScriptResource'
MAX_DOWNLOAD_SIZE = 512 * 1024 * 1024
SECRET_MASK = '******'
ERROR_MESSAGES = {
    7001: 'Mirror酱 CDK 已过期，请更换或续费。',
    7002: 'Mirror酱 CDK 无效，请检查输入。',
    7003: 'Mirror酱 CDK 今日下载次数已达上限，请明日再试或切换更新源。',
    7004: 'Mirror酱 CDK 类型与本资源不匹配。',
    7005: 'Mirror酱 CDK 已被封禁，请联系 Mirror酱售后。',
    8001: 'Mirror酱当前平台或通道暂无可用资源，请切换更新源。',
    8009: '此 Mirror酱 CDK 仅支持流程测试，不支持实际下载。',
}


class MirrorError(ExecutionError):
    def __init__(self, message, code=None):
        super().__init__(message, error_code=code)


def secret_path(config_file):
    return Path(config_file).resolve().parent / '.security' / 'mirrorchyan.cdk'


def read_cdk(config_file):
    path = secret_path(config_file)
    try:
        value = path.read_text(encoding='ascii').strip() if path.exists() else ''
    except (OSError, UnicodeError):
        raise MirrorError('无法读取 Mirror酱 CDK，请检查私有配置文件。') from None
    if value and not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value):
        raise MirrorError('Mirror酱 CDK 文件格式错误，请重新保存。')
    return value


def save_cdk(config_file, value):
    if not isinstance(value, str):
        raise ValueError('CDK must be a string.')
    value = value.strip()
    if value == SECRET_MASK:
        return bool(read_cdk(config_file))
    if value and not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value):
        raise ValueError('CDK must contain only letters, numbers, underscores or hyphens (up to 256 characters).')
    path = secret_path(config_file)
    if not value:
        path.unlink(missing_ok=True)
        return False
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.mirror-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(value + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def latest(rid, current_version='', cdk='', platform=None):
    params = {'channel': 'stable', 'user_agent': 'nkas'}
    if current_version:
        params['current_version'] = current_version
    if cdk:
        params['cdk'] = cdk
    params.update(platform or {})
    try:
        response = requests.get(f'{API_ROOT}/{rid}/latest', params=params, timeout=(10, 20))
        payload = response.json()
    except (requests.RequestException, ValueError):
        # requests exceptions include the complete query URL, which contains the CDK.
        raise MirrorError('Mirror酱请求失败，请检查网络或稍后重试。') from None
    if (not isinstance(payload, dict) or isinstance(payload.get('code'), bool)
            or not isinstance(payload.get('code'), int)):
        raise MirrorError('Mirror酱返回了无效的版本响应。')
    code = payload['code']
    if code or not response.ok:
        message = ERROR_MESSAGES.get(code, f'Mirror酱服务错误（HTTP {response.status_code}，code {code}）。')
        raise MirrorError(message, code=code)
    data = payload.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('version_name'), str) or not data['version_name']:
        raise MirrorError('Mirror酱响应缺少版本信息。')
    return data


def download(data, destination, expected_type='pack', max_size=MAX_DOWNLOAD_SIZE):
    if not data.get('url'):
        raise MirrorError('下载需要有效的 Mirror酱 CDK，请在部署页填写，或切换原更新源。')
    url = data['url']
    size = data.get('filesize')
    digest = data.get('sha256')
    try:
        scheme = urlparse(url).scheme if isinstance(url, str) else ''
    except ValueError:
        scheme = ''
    if (scheme != 'https'
            or isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= max_size
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest)
            or data.get('update_type') != expected_type):
        raise MirrorError('Mirror酱下载元数据无效或包型不匹配。')
    destination = Path(destination)
    hashed = hashlib.sha256()
    total = 0
    try:
        with requests.get(url, stream=True, timeout=(10, 120)) as response:
            if not response.ok:
                try:
                    payload = response.json()
                    code = payload.get('code') if isinstance(payload, dict) else None
                except ValueError:
                    code = None
                if isinstance(code, bool) or not isinstance(code, int):
                    code = None
                message = ERROR_MESSAGES.get(
                    code, f'Mirror酱下载失败（HTTP {response.status_code}），请重新检查更新。',
                )
                raise MirrorError(message, code=code)
            with destination.open('wb') as stream:
                for block in response.iter_content(128 * 1024):
                    total += len(block)
                    if total > size:
                        raise MirrorError('Mirror酱下载大小超出声明值。')
                    hashed.update(block)
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
    except (requests.RequestException, OSError):
        destination.unlink(missing_ok=True)
        raise MirrorError('Mirror酱下载失败，请检查网络和磁盘权限，然后重新检查更新。') from None
    except MirrorError:
        destination.unlink(missing_ok=True)
        raise
    if total != size or hashed.hexdigest().lower() != digest.lower():
        destination.unlink(missing_ok=True)
        raise MirrorError('Mirror酱下载大小或 SHA-256 校验失败，已拒绝安装。')
    return destination


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.mirror-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
