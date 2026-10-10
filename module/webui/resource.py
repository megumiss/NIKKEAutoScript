"""资源仓库（章节地图等运行数据）的本地同步：独立于主项目更新，按需检查并拉取最新版本。

本地副本固定在 data/resources，布局与远端仓库一致：version.json 记录整体版本与各类数据的版本，
maps/ 存放章节地图，后续新增的数据以同级目录加入。有 git 时按分支浅克隆或快进到远端，
没有 git 时退化为下载 GitHub 分支压缩包；同步在后台线程执行，界面轮询状态。
"""

import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from dev_tools.map_paths import RESOURCE_ROOT
from module.logger import logger
from module.webui.setting import State

STATE_FILE = '.resource_state.json'
VERSION_FILE = 'version.json'
DEFAULT_BRANCHES = ('main', 'master')
CHECK_CACHE_SECONDS = 60
GITHUB_URL = re.compile(r'^https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$')
SYNC_ERRORS = (OSError, subprocess.SubprocessError, ValueError, zipfile.BadZipFile)


class ResourceManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = 'idle'
        self.error = ''
        self.log = []
        self.remote = None
        self.thread = None

    @property
    def repository(self):
        return str(getattr(State.deploy_config, 'ResourceRepository', '') or '').strip()

    def git_executable(self):
        """优先使用部署配置里的 git，不存在时退回 PATH 上的 git；都没有则返回 None。"""
        try:
            configured = State.deploy_config.filepath('GitExecutable')
        except (KeyError, TypeError, AttributeError):
            configured = None
        if configured and os.path.exists(configured):
            return configured
        return shutil.which('git')

    def git_options(self):
        options = []
        proxy = getattr(State.deploy_config, 'GitProxy', None)
        if proxy:
            options += ['-c', f'http.proxy={proxy}', '-c', f'https.proxy={proxy}']
        if getattr(State.deploy_config, 'SSLVerify', True) is False:
            options += ['-c', 'http.sslVerify=false']
        return options

    def _log(self, message):
        logger.info(f'[Resource] {message}')
        with self.lock:
            self.log.append(message)
            del self.log[:-50]

    @staticmethod
    def _run(args, timeout, cwd=None):
        completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                   timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            raise ValueError(f'{Path(args[0]).name} {args[-2] if len(args) > 2 else ""} 失败：{detail[-1] if detail else completed.returncode}')
        return completed.stdout

    # ---- 本地与远端信息 ----
    def local(self):
        info = dict(path=str(RESOURCE_ROOT), exists=False, version=None, sha=None, branch=None, updated_at=None,
                    synced_at=None, components={}, source=None)
        version_file = RESOURCE_ROOT / VERSION_FILE
        if not version_file.is_file():
            return info
        info['exists'] = True
        try:
            data = json.loads(version_file.read_text(encoding='utf-8'))
            info.update(version=data.get('version'), updated_at=data.get('updated_at'),
                        components=data.get('components', {}))
        except (OSError, ValueError) as exc:
            info['error'] = f'version.json 无法读取：{exc}'
        state_file = RESOURCE_ROOT / STATE_FILE
        if state_file.is_file():
            try:
                saved = json.loads(state_file.read_text(encoding='utf-8'))
                info.update(sha=saved.get('sha'), branch=saved.get('branch'), source=saved.get('source'),
                            synced_at=saved.get('synced_at'))
            except (OSError, ValueError):
                pass
        return info

    def remote_info(self, force=False):
        """远端最新提交与默认分支；优先 git ls-remote，没有 git 时读取 GitHub raw 的 version.json。"""
        repository = self.repository
        with self.lock:
            cached = self.remote
        if (cached and not force and cached.get('repository') == repository
                and time.time() - cached['checked_at'] < CHECK_CACHE_SECONDS):
            return cached
        result = dict(repository=repository, checked_at=time.time(), sha=None, branch=None, version=None,
                      error=None, method=None)
        if not repository:
            result['error'] = '未配置资源仓库地址。'
        else:
            git = self.git_executable()
            try:
                if git:
                    result['method'] = 'git'
                    output = self._run([git, *self.git_options(), 'ls-remote', '--symref', repository, 'HEAD',
                                        *[f'refs/heads/{name}' for name in DEFAULT_BRANCHES]], timeout=60)
                    result.update(zip(('branch', 'sha'), self._parse_ls_remote(output)))
                else:
                    result['method'] = 'zip'
                    branch, data = self._fetch_remote_version(repository)
                    result.update(branch=branch, version=data.get('version'))
            except SYNC_ERRORS as exc:
                result['error'] = f'无法访问资源仓库：{exc}'
        with self.lock:
            self.remote = result
        return result

    @staticmethod
    def _parse_ls_remote(output):
        branch, heads = None, {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            if parts[0] == 'ref:' and parts[1].startswith('refs/heads/'):
                branch = parts[1][len('refs/heads/'):]
            elif parts[1].startswith('refs/heads/'):
                heads[parts[1][len('refs/heads/'):]] = parts[0]
        if branch is None:
            branch = next((name for name in DEFAULT_BRANCHES if name in heads), None)
        if branch is None or branch not in heads:
            raise ValueError('远端资源仓库没有可用分支（仓库可能为空）。')
        return branch, heads[branch]

    @staticmethod
    def _github_parts(repository):
        match = GITHUB_URL.match(repository)
        if not match:
            raise ValueError('没有可用的 git 时，只支持 GitHub 地址的资源仓库。')
        return match.group(1), match.group(2)

    def _opener(self):
        handlers = []
        proxy = getattr(State.deploy_config, 'GitProxy', None)
        if proxy and str(proxy).startswith('http'):
            handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
        if getattr(State.deploy_config, 'SSLVerify', True) is False:
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        return urllib.request.build_opener(*handlers)

    def _fetch_remote_version(self, repository):
        owner, name = self._github_parts(repository)
        opener = self._opener()
        last = None
        for branch in DEFAULT_BRANCHES:
            url = f'https://raw.githubusercontent.com/{owner}/{name}/{branch}/{VERSION_FILE}'
            try:
                with opener.open(url, timeout=30) as response:
                    return branch, json.loads(response.read().decode('utf-8'))
            except (OSError, ValueError) as exc:
                last = exc
        raise ValueError(f'读取远端 version.json 失败：{last}')

    def status(self, check=True):
        local = self.local()
        remote = self.remote_info() if check else (self.remote or {})
        up_to_date = None
        if local['exists'] and remote and not remote.get('error'):
            if remote.get('sha') and local.get('sha'):
                up_to_date = remote['sha'] == local['sha']
            elif remote.get('version') and local.get('version'):
                up_to_date = remote['version'] == local['version']
        if not local['exists']:
            condition = 'missing'
        elif up_to_date is None:
            condition = 'unknown'
        else:
            condition = 'latest' if up_to_date else 'outdated'
        with self.lock:
            return dict(state=self.state, syncing=self.state == 'syncing', condition=condition, local=local,
                        remote=remote, error=self.error, log=list(self.log[-20:]), repository=self.repository,
                        git=bool(self.git_executable()))

    # ---- 同步 ----
    def start_sync(self):
        with self.lock:
            if self.state == 'syncing':
                return False
            self.state, self.error, self.log = 'syncing', '', []
            self.thread = threading.Thread(target=self._sync, name='resource-sync', daemon=True)
            self.thread.start()
        return True

    def _sync(self):
        try:
            self._log('开始同步资源仓库…')
            remote = self.remote_info(force=True)
            if remote.get('error'):
                raise ValueError(remote['error'])
            branch = remote.get('branch') or DEFAULT_BRANCHES[0]
            git = self.git_executable()
            RESOURCE_ROOT.parent.mkdir(parents=True, exist_ok=True)
            if git:
                sha = self._sync_with_git(git, remote['repository'], branch)
            else:
                sha = self._sync_with_zip(remote['repository'], branch)
            self._write_state(remote['repository'], branch, sha)
            self._log('资源同步完成。')
            with self.lock:
                self.state, self.remote = 'finish', None
        except SYNC_ERRORS as exc:
            logger.exception(exc)
            with self.lock:
                self.state, self.error = 'failed', str(exc)

    def _sync_with_git(self, git, repository, branch):
        base = [git, *self.git_options()]
        root = str(RESOURCE_ROOT)
        if (RESOURCE_ROOT / '.git').is_dir():
            # 开发机上的本地副本可能带有未提交或未推送的改动，覆盖前先拒绝，避免丢失工作。
            if self._run([*base, '-C', root, 'status', '--porcelain', '-uno'], timeout=60).strip():
                raise ValueError('本地资源目录有未提交的改动，已停止同步以免覆盖。')
            self._log(f'更新已有仓库（{branch}）…')
            self._run([*base, '-C', root, 'remote', 'set-url', 'origin', repository], timeout=30)
            self._run([*base, '-C', root, 'fetch', '--depth=1', 'origin', branch], timeout=1800)
            if self._run([*base, '-C', root, 'rev-list', '--count', 'FETCH_HEAD..HEAD'], timeout=60).strip() != '0':
                raise ValueError('本地资源仓库有未推送的提交，已停止同步以免覆盖。')
            self._run([*base, '-C', root, 'reset', '--hard', 'FETCH_HEAD'], timeout=600)
            self._run([*base, '-C', root, 'clean', '-fdq', '-e', STATE_FILE], timeout=600)
        else:
            staged = RESOURCE_ROOT.with_name(RESOURCE_ROOT.name + '.part')
            shutil.rmtree(staged, ignore_errors=True)
            self._log(f'克隆 {repository}（{branch}）…')
            self._run([*base, 'clone', '--depth=1', '--branch', branch, repository, str(staged)], timeout=3600)
            self._replace_root(staged)
        return self._run([*base, '-C', root, 'rev-parse', 'HEAD'], timeout=30).strip()

    def _sync_with_zip(self, repository, branch):
        owner, name = self._github_parts(repository)
        url = f'https://codeload.github.com/{owner}/{name}/zip/refs/heads/{branch}'
        self._log(f'没有可用的 git，下载压缩包 {url} …')
        with tempfile.TemporaryDirectory(prefix='resources-', dir=RESOURCE_ROOT.parent) as temporary:
            temporary = Path(temporary)
            archive = temporary / 'resource.zip'
            self._download(url, archive)
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                if not names:
                    raise ValueError('下载的资源包为空。')
                for member in names:
                    if Path(member).is_absolute() or '..' in Path(member).parts:
                        raise ValueError(f'资源包含非法路径：{member}')
                bundle.extractall(temporary)
            extracted = temporary / names[0].split('/')[0]
            if not (extracted / VERSION_FILE).is_file():
                raise ValueError('下载的资源包缺少 version.json。')
            staged = RESOURCE_ROOT.with_name(RESOURCE_ROOT.name + '.part')
            shutil.rmtree(staged, ignore_errors=True)
            shutil.move(str(extracted), str(staged))
        self._replace_root(staged)
        return None

    def _download(self, url, target):
        opener = self._opener()
        with opener.open(url, timeout=120) as response, open(target, 'wb') as stream:
            total, reported = 0, 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                stream.write(chunk)
                total += len(chunk)
                if total - reported >= 20 << 20:
                    reported = total
                    self._log(f'已下载 {total / (1 << 20):.0f} MB…')
        self._log(f'下载完成，共 {total / (1 << 20):.1f} MB。')

    def _replace_root(self, staged):
        """先把旧目录挪开再换上新目录，失败时还原，避免出现半成品的资源目录。"""
        old = RESOURCE_ROOT.with_name(RESOURCE_ROOT.name + '.old')
        shutil.rmtree(old, ignore_errors=True)
        if RESOURCE_ROOT.exists():
            os.replace(RESOURCE_ROOT, old)
        try:
            os.replace(staged, RESOURCE_ROOT)
        except OSError:
            if old.exists() and not RESOURCE_ROOT.exists():
                os.replace(old, RESOURCE_ROOT)
            raise
        shutil.rmtree(old, ignore_errors=True)

    def _write_state(self, repository, branch, sha):
        version = None
        try:
            version = json.loads((RESOURCE_ROOT / VERSION_FILE).read_text(encoding='utf-8')).get('version')
        except (OSError, ValueError):
            pass
        state = dict(source=repository, branch=branch, sha=sha, version=version,
                     synced_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        (RESOURCE_ROOT / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n',
                                                encoding='utf-8')


resource_manager = ResourceManager()
