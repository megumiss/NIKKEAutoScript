"""MirrorChyan transport and private CDK storage shared by both updaters."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = 'https://mirrorchyan.com/api/resources'
SOURCE_RID = 'NKAS_repo'
DESKTOP_RID = 'NKAS'
MAX_PACKAGE = 1024 * 1024 * 1024


class MirrorError(RuntimeError):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def credential_path(root):
    return Path(root) / 'config/.security/mirrorchyan.acc'


def save_cdk(root, value=None, clear=False):
    path = credential_path(root)
    if clear:
        path.unlink(missing_ok=True)
        return
    if not isinstance(value, str) or len(value) > 512:
        raise MirrorError('CDK 必须是长度不超过 512 的文本')
    value = value.strip()
    if not value or all(char in '*•' for char in value):
        return
    from module.config.account import _derive_key_from_username, _encrypt_field

    atomic_json(path, {'cdk': _encrypt_field(_derive_key_from_username(), value)})


def load_cdk(root):
    if not credential_path(root).exists():
        return ''
    from module.config.account import _decrypt_field, _derive_key_from_username

    try:
        value = read_json(credential_path(root))
        return _decrypt_field(_derive_key_from_username(), value['cdk']) or ''
    except (OSError, ValueError, KeyError, TypeError):
        raise MirrorError('无法读取 Mirror 酱 CDK，请重新保存或清除') from None


def enabled(root):
    from deploy.utils import poor_yaml_read

    value = poor_yaml_read(str(Path(root) / 'config/deploy.yaml')).get('MirrorChyanEnabled', False)
    if not isinstance(value, bool):
        raise MirrorError('MirrorChyanEnabled 必须为 true 或 false，请检查部署配置')
    return value


def safe_message(message, secret=''):
    message = str(message)[:1000]
    if secret:
        for value in (secret, urllib.parse.quote(secret), urllib.parse.quote_plus(secret)):
            message = message.replace(value, '[CDK]')
    return re.sub(r'https?://\S+', '[URL]', message)


class Client:
    def __init__(self, root, api=API, cdk=None):
        self.root = Path(root)
        self.api = api.rstrip('/')
        self.cdk = load_cdk(root) if cdk is None else cdk

    def latest(self, resource, current='', full=False):
        params = {'channel': 'stable', 'user_agent': 'NKAS'}
        if current and not full:
            params['current_version'] = current
        if self.cdk:
            params['cdk'] = self.cdk
        if resource == DESKTOP_RID:
            params.update(os='windows', arch='amd64')
        url = f'{self.api}/{resource}/latest?{urllib.parse.urlencode(params)}'
        try:
            try:
                response = urllib.request.urlopen(url, timeout=20)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise MirrorError('Mirror 酱响应过大')
                data = json.loads(raw)
                if not isinstance(data, dict) or not isinstance(data.get('code'), int):
                    raise MirrorError('Mirror 酱响应格式错误')
                if data['code'] != 0:
                    reason = safe_message(data.get('msg', '请求失败'), self.cdk)
                    raise MirrorError(f'Mirror 酱错误 {data["code"]}：{reason}')
                if response.status != 200:
                    raise MirrorError(f'Mirror 酱 HTTP 错误 {response.status}')
        except (urllib.error.URLError, TimeoutError, OSError):
            raise MirrorError('Mirror 酱连接失败或超时，请检查网络；使用原渠道需手动关闭开关') from None
        except (ValueError, UnicodeError):
            raise MirrorError('Mirror 酱未返回有效 JSON') from None
        result = data.get('data')
        if not isinstance(result, dict) or not isinstance(result.get('version_name'), str):
            raise MirrorError('Mirror 酱缺少版本信息')
        if resource == SOURCE_RID and not re.fullmatch(r'[0-9a-f]{40}', result['version_name']):
            raise MirrorError('Mirror 酱源码版本必须是完整 commit SHA')
        return result

    @staticmethod
    def require_download(info):
        if not info.get('url'):
            raise MirrorError('有新版本，但 Mirror 酱未返回下载链接；请检查 CDK，或手动关闭 Mirror 酱开关')
        if not isinstance(info['url'], str):
            raise MirrorError('Mirror 酱下载链接无效')

    def download(self, info, target, progress=None):
        self.require_download(info)
        size, digest = info.get('filesize'), info.get('sha256')
        if type(size) is not int or not 0 < size <= MAX_PACKAGE:
            raise MirrorError('Mirror 酱更新包大小无效')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
            raise MirrorError('Mirror 酱更新包缺少有效 SHA-256')
        if urllib.parse.urlsplit(info['url']).scheme not in ('http', 'https'):
            raise MirrorError('Mirror 酱下载链接无效')
        total, hasher = 0, hashlib.sha256()
        deadline = time.monotonic() + 600
        try:
            with urllib.request.urlopen(info['url'], timeout=30) as response, open(target, 'wb') as stream:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > size or time.monotonic() > deadline:
                        raise MirrorError('Mirror 酱更新包超过声明大小或下载超时')
                    stream.write(chunk)
                    hasher.update(chunk)
                    if progress:
                        progress(total, size)
                stream.flush()
                os.fsync(stream.fileno())
        except (urllib.error.URLError, TimeoutError, OSError):
            raise MirrorError('Mirror 酱下载失败，请重新检查更新获取下载链接') from None
        if total != size or hasher.hexdigest() != digest.lower():
            raise MirrorError('Mirror 酱更新包大小或 SHA-256 校验失败')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['desktop-check', 'desktop-stage'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--current', required=True)
    parser.add_argument('--target')
    args = parser.parse_args()
    try:
        if not enabled(args.root):
            raise MirrorError('Mirror 酱开关已关闭，请重新检查更新')
        client = Client(args.root)
        info = client.latest(DESKTOP_RID, args.current, full=args.action == 'desktop-stage')
        result = {
            'version': info['version_name'],
            'downloadable': bool(info.get('url')),
            'release_note': safe_message(info.get('release_note', ''), client.cdk),
        }
        if args.action == 'desktop-stage':
            if args.target != info['version_name']:
                raise MirrorError('Mirror 酱目标版本已变化，请重新检查更新')
            from deploy.update_package import stage_desktop

            result.update(stage_desktop(client, info))
        print('NKAS_MIRROR_RESULT=' + json.dumps(result, ensure_ascii=True))
    except (MirrorError, OSError, ValueError) as error:
        print('NKAS_MIRROR_RESULT=' + json.dumps({'error': safe_message(error)}, ensure_ascii=True))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
