"""资源仓库（章节地图等运行数据）的本地同步：独立于主项目更新，按需检查并拉取最新版本。

本地副本固定在 data/resources，布局与远端仓库一致：version.json 记录整体版本与各类数据的版本，
maps/ 存放章节地图，后续新增的数据以同级目录加入。更新源为 Mirror酱时下载增量或完整资源包，
否则有 git 时按分支浅克隆或快进到远端，没有 git 时退化为下载 GitHub 分支压缩包；
同步在后台线程执行，界面轮询状态。
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
from pathlib import Path, PurePosixPath

from deploy.mirrorchyan import RESOURCE_RID, MirrorError, download, latest, read_cdk
from module.campaign.map.paths import RESOURCE_ROOT
from module.logger import logger
from module.webui.setting import State

STATE_FILE = '.resource_state.json'
VERSION_FILE = 'version.json'
DEFAULT_BRANCHES = ('main', 'master')
CHECK_CACHE_SECONDS = 60
GITHUB_URL = re.compile(r'^https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$')
SYNC_ERRORS = (OSError, subprocess.SubprocessError, ValueError, zipfile.BadZipFile)


class ResourceManager:
    """进程内唯一的资源仓库管理器：查询版本、在后台线程同步本地副本。

    状态（state/error/log）要被界面轮询和同步线程同时读写，统一用 self.lock 保护；同步本体在
    daemon 线程里跑，调用方只能通过 status 观察进度，不要直接 join。
    远端信息有 CHECK_CACHE_SECONDS 的缓存，避免界面轮询把远端打满。
    """

    def __init__(self):
        """初始化同步状态与日志缓冲；真正的目录检查推迟到首次 status/同步时。"""
        self.lock = threading.Lock()
        self.state = 'idle'
        self.error = ''
        self.log = []
        self.remote = None
        self.thread = None

    @property
    def repository(self):
        """部署配置里的资源仓库地址，未配置时为空串。

        配置项可能不存在或为 None，这里统一成去掉空白的字符串，调用方只需判断真假。
        """
        return str(getattr(State.deploy_config, 'ResourceRepository', '') or '').strip()

    @property
    def use_mirror(self):
        """是否按部署页的统一更新源使用 Mirror酱同步资源。"""
        return getattr(State.deploy_config, 'UpdateSource', 'git') == 'mirrorchyan'

    @staticmethod
    def _mirror_version(data):
        """读取 Mirror酱版本并去掉发布标签的 `v` 前缀，与 version.json 保持同一格式。"""
        version = data['version_name']
        version = version[1:] if version.startswith('v') else version
        if not version:
            raise MirrorError('Mirror酱响应缺少有效的资源版本。')
        return version

    def _mirror_latest(self, current_version=None):
        """按本地版本查询不分平台的资源包，由服务端选择增量包或完整包。

        Mirror 发布标签带 `v`，本地 version.json 不带；查询时必须恢复原格式才能命中增量。
        CDK 只用于本次请求，不写入远端缓存或同步状态。
        """
        if current_version and not current_version.startswith('v'):
            current_version = f'v{current_version}'
        return latest(RESOURCE_RID, current_version=current_version or '', cdk=read_cdk(State.deploy_config.file))

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
        """复用主项目部署配置的代理与 SSL 设置，返回插在 git 子命令前的 -c 选项。

        用 `-c` 临时覆盖而不是写进资源仓库的 .git/config：同步目录随时可能被整体替换，
        写进配置的设置会跟着丢，而且不该影响用户自己的 git 环境。
        """
        options = []
        proxy = getattr(State.deploy_config, 'GitProxy', None)
        if proxy:
            options += ['-c', f'http.proxy={proxy}', '-c', f'https.proxy={proxy}']
        if getattr(State.deploy_config, 'SSLVerify', True) is False:
            options += ['-c', 'http.sslVerify=false']
        return options

    def _log(self, message):
        """记录一条同步进度，同时进日志文件和界面可读的环形缓冲。

        缓冲只保留最近 50 条，避免长时间下载把内存撑大；界面轮询最近日志展示同步进度。
        """
        logger.info(f'[Resource] {message}')
        with self.lock:
            self.log.append(message)
            del self.log[:-50]

    @staticmethod
    def _run(args, timeout, cwd=None):
        """执行一条外部命令并返回 stdout，非零退出码一律转成带末行错误信息的 ValueError。

        取 stderr/stdout 的最后一行是因为 git 的实际原因通常在末行，前面都是进度输出。
        Windows 上加 CREATE_NO_WINDOW，否则每条 git 命令都会闪一个控制台窗口。
        超时由调用方按操作量级给：ls-remote 几十秒，克隆和下载需要上千秒。
        """
        completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                   timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            raise ValueError(f'{Path(args[0]).name} {args[-2] if len(args) > 2 else ""} 失败：{detail[-1] if detail else completed.returncode}')
        return completed.stdout

    # ---- 本地与远端信息 ----
    def local(self):
        """本地资源副本的信息：版本、各类数据的版本、来源分支与上次同步时间。

        以 version.json 是否存在作为「有本地资源」的判据；它来自仓库本身，记录版本与组件。
        .resource_state.json 是同步时本地写的，记录来源仓库、分支、提交和时间，可能缺失（手工
        拷贝的副本）或损坏，所以单独读、读坏了就当没有，不影响 exists 与版本号。
        version.json 读不出来时在 error 里留信息而不抛错，让界面能提示「资源已损坏」。
        """
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
        """按更新源查询 Mirror酱版本，或资源仓库的提交、分支与压缩包版本。"""
        repository = self.repository
        mirror = self.use_mirror
        source = RESOURCE_RID if mirror else repository
        with self.lock:
            cached = self.remote
        if (cached and not force and cached.get('source') == source
                and time.time() - cached['checked_at'] < CHECK_CACHE_SECONDS):
            return cached
        result = dict(repository=repository, source=source, checked_at=time.time(), sha=None, branch=None, version=None,
                      error=None, method=None)
        try:
            if mirror:
                result['method'] = 'mirrorchyan'
                result['version'] = self._mirror_version(self._mirror_latest())
            elif not repository:
                result['error'] = '未配置资源仓库地址。'
            else:
                git = self.git_executable()
                if git:
                    result['method'] = 'git'
                    output = self._run([git, *self.git_options(), 'ls-remote', '--symref', repository, 'HEAD',
                                        *[f'refs/heads/{name}' for name in DEFAULT_BRANCHES]], timeout=60)
                    result.update(zip(('branch', 'sha'), self._parse_ls_remote(output)))
                else:
                    result['method'] = 'zip'
                    branch, data = self._fetch_remote_version(repository)
                    result.update(branch=branch, version=data.get('version'))
        except MirrorError as exc:
            result['error'] = str(exc)
        except SYNC_ERRORS as exc:
            result['error'] = f'无法访问资源仓库：{exc}'
        with self.lock:
            self.remote = result
        return result

    @staticmethod
    def _parse_ls_remote(output):
        """从 `ls-remote --symref` 的输出里解出（默认分支, 提交）。

        带 `ref:` 的那行才是 HEAD 指向的真实默认分支，优先采用；仓库没给 symref 时退回按
        DEFAULT_BRANCHES 的顺序挑一个存在的分支。一个分支都解不出来通常是空仓库或地址写错，
        这种情况必须报错，否则后面会拿着空分支名去克隆。
        """
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
        """从仓库地址里解出 (owner, name)，供拼接 raw 与压缩包下载地址使用。

        只有没装 git 时才会走到这里，而退化路径依赖 GitHub 的 raw/codeload 接口，所以非
        GitHub 地址直接报错说明原因，而不是拼出一个必然 404 的地址。
        """
        match = GITHUB_URL.match(repository)
        if not match:
            raise ValueError('没有可用的 git 时，只支持 GitHub 地址的资源仓库。')
        return match.group(1), match.group(2)

    def _opener(self):
        """按部署配置构造带代理与 SSL 设置的 urllib opener。

        与 git 路径共用同一份配置，避免「git 能连、压缩包下载连不上」。只有 http(s) 代理能给
        urllib 用，socks 代理会被忽略（装了 git 的机器走不到这条路径）。
        """
        handlers = []
        proxy = getattr(State.deploy_config, 'GitProxy', None)
        if proxy and str(proxy).startswith('http'):
            handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
        if getattr(State.deploy_config, 'SSLVerify', True) is False:
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        return urllib.request.build_opener(*handlers)

    def _fetch_remote_version(self, repository):
        """没有 git 时，从 GitHub raw 读远端 version.json，返回（分支, 文档）。

        raw 地址必须带分支名，而此时无法查询默认分支，只能按 DEFAULT_BRANCHES 依次试；
        全部失败才报错，并带上最后一次的原因（多为 404 或网络不可达）。
        这条路径拿不到提交号，所以版本比较只能按 version 字段做。
        """
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
        """汇总本地、远端与同步状态，供界面展示。

        condition 是给调用方的结论：missing（本地没有）、outdated（有新版本）、latest（最新）、
        unknown（无法比较）。比较优先用提交号，只有 zip 路径没有提交号时才退回 version 字段；
        任一端缺少可比字段就是 unknown。check=False 只读缓存的远端信息，用于同步过程中的高频轮询。
        """
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
        """启动后台同步线程；已有同步在跑时返回 False 而不排队。

        状态切换和起线程必须在同一把锁内完成，否则多个调用方可能同时判断为空闲、起两个线程
        去写同一个资源目录。返回 False 的调用方应改为等待当前同步结束。
        """
        with self.lock:
            if self.state == 'syncing':
                return False
            self.state, self.error, self.log = 'syncing', '', []
            self.thread = threading.Thread(target=self._sync, name='resource-sync', daemon=True)
            self.thread.start()
        return True

    def _sync(self):
        """同步线程主体：按更新源选择 Mirror酱完整包、Git 克隆/快进或 GitHub 压缩包。

        强制刷新一次远端信息，不能用缓存——同步前必须确认远端版本。完成后把 remote 缓存清掉，
        让下一次 status 重新查询，否则界面会拿旧的远端信息和新的本地版本比较出「仍需更新」。
        预期错误记进 error 字段，线程不向外抛；Mirror 异常不打印可能含凭据的请求帧。
        """
        try:
            self._log('开始同步资源…')
            remote = self.remote_info(force=True)
            if remote.get('error'):
                raise ValueError(remote['error'])
            RESOURCE_ROOT.parent.mkdir(parents=True, exist_ok=True)
            if remote['method'] == 'mirrorchyan':
                self._sync_with_mirror()
                self._write_state(f'mirrorchyan:{RESOURCE_RID}', None, None)
            else:
                branch = remote.get('branch') or DEFAULT_BRANCHES[0]
                git = self.git_executable()
                if git:
                    sha = self._sync_with_git(git, remote['repository'], branch)
                else:
                    sha = self._sync_with_zip(remote['repository'], branch)
                self._write_state(remote['repository'], branch, sha)
            self._log('资源同步完成。')
            with self.lock:
                self.state, self.remote = 'finish', None
        except MirrorError as exc:
            logger.warning(f'[Resource] Mirror酱同步失败：{exc}')
            with self.lock:
                self.state, self.error = 'failed', str(exc)
        except SYNC_ERRORS as exc:
            if self.use_mirror:
                logger.warning(f'[Resource] Mirror酱同步失败：{exc}')
            else:
                logger.exception(exc)
            with self.lock:
                self.state, self.error = 'failed', str(exc)

    @staticmethod
    def _resource_path(name):
        """校验资源 ZIP 与 changes.json 使用的 POSIX 相对路径并返回路径对象。

        拒绝绝对路径、盘符、反斜杠、空段和 `..`，同时禁止包修改 Git 元数据与本地同步状态；
        所有路径先经过这里，再映射到本次新建的临时目录，防止写出资源目录。
        """
        if not isinstance(name, str) or not name or '\x00' in name:
            raise ValueError(f'资源包含非法路径：{name}')
        parts = name.split('/')
        path = PurePosixPath(name)
        if (path.is_absolute() or '\\' in name or ':' in name
                or any(part in ('', '.', '..') for part in parts)
                or parts[0].lower() == '.git' or name == STATE_FILE):
            raise ValueError(f'资源包含非法路径：{name}')
        return path

    @classmethod
    def _extract_resource_bundle(cls, archive, target):
        """校验 ZIP 条目与符号链接后解压到新建临时目录，空包和越界路径直接失败。"""
        target.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if not entries:
                raise ValueError('下载的资源包为空。')
            for entry in entries:
                cls._resource_path(entry.filename.rstrip('/'))
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f'资源包包含符号链接：{entry.filename}')
            bundle.extractall(target)

    @staticmethod
    def _validate_resource_version(directory, version):
        """要求安装结果存在 version.json，且版本与本次 Mirror API 响应完全一致。"""
        version_file = directory / VERSION_FILE
        if not version_file.is_file():
            raise ValueError('下载的资源包缺少 version.json。')
        package_version = json.loads(version_file.read_text(encoding='utf-8')).get('version')
        if package_version != version:
            raise ValueError(f'资源包版本不一致：Mirror酱为 {version}，包内为 {package_version}。')

    @classmethod
    def _apply_incremental(cls, archive, temporary):
        """在旧资源副本上应用 changes.json，返回可原子替换的新目录。

        删除文件和目录后再写入新增、修改文件，未变更文件从旧副本保留。所有操作只发生在
        临时目录；复制时排除 .git 与同步状态，使 Mirror 更新和后续状态写入保持独立。
        """
        extracted = temporary / 'incremental'
        cls._extract_resource_bundle(archive, extracted)
        changes_file = extracted / 'changes.json'
        if not changes_file.is_file():
            raise ValueError('增量资源包缺少 changes.json。')
        changes = json.loads(changes_file.read_text(encoding='utf-8'))
        if not isinstance(changes, dict):
            raise ValueError('增量资源包 changes.json 格式无效。')
        keys = ('added', 'modified', 'deleted', 'added_dir', 'deleted_dir')
        for key in keys:
            values = changes.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f'增量资源包 {key} 必须是数组。')
            paths = []
            for value in values:
                path = cls._resource_path(value)
                if path == PurePosixPath('changes.json'):
                    raise ValueError('增量资源包不能把 changes.json 作为安装文件。')
                paths.append(path)
            changes[key] = paths

        if not RESOURCE_ROOT.is_dir():
            raise ValueError('本地资源不存在，不能安装增量包。')
        updated = temporary / 'updated'
        shutil.copytree(RESOURCE_ROOT, updated, ignore=shutil.ignore_patterns('.git', STATE_FILE))
        for path in changes['deleted']:
            target = updated / path
            if target.is_dir() and not target.is_symlink():
                raise ValueError(f'增量删除项不是文件：{path}')
            target.unlink(missing_ok=True)
        for path in sorted(changes['deleted_dir'], key=lambda value: len(value.parts), reverse=True):
            target = updated / path
            if target.is_symlink():
                target.unlink()
            elif target.exists():
                shutil.rmtree(target)
        for path in changes['added_dir']:
            (updated / path).mkdir(parents=True, exist_ok=True)
        for path in [*changes['added'], *changes['modified']]:
            source = extracted / path
            if not source.is_file():
                raise ValueError(f'增量资源包缺少文件：{path}')
            target = updated / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        return updated

    def _sync_with_mirror(self):
        """从 Mirror酱下载并安装本次响应指定的增量包或完整包。

        有本地版本时按 Mirror 的 `v` 标签格式查询增量；已是最新但用户仍点击同步时，重新
        请求完整包以保留修复资源的能力。安装前核对包内版本，最终原子替换资源目录，CDK
        和签名 URL 仅存在于共享 Mirror 客户端的调用内，不进入界面状态或日志。
        """
        local = self.local()
        current_version = local.get('version') if local['exists'] else None
        if not isinstance(current_version, str):
            current_version = None
        data = self._mirror_latest(current_version)
        version = self._mirror_version(data)
        if current_version == version and not data.get('url'):
            data = self._mirror_latest()
            version = self._mirror_version(data)
        update_type = data.get('update_type')
        if data.get('url') and update_type not in ('full', 'incremental'):
            raise MirrorError('Mirror酱返回了不支持的资源包型。')
        package_kind = '增量' if update_type == 'incremental' else '完整'
        self._log(f'从 Mirror酱下载{package_kind}资源 {version}…')
        with tempfile.TemporaryDirectory(prefix='resources-', dir=RESOURCE_ROOT.parent) as temporary:
            temporary = Path(temporary)
            archive = temporary / 'resource.zip'
            download(data, archive, expected_type=update_type)
            if update_type == 'incremental':
                if not current_version:
                    raise ValueError('没有可应用的本地版本，不能安装增量包。')
                updated = self._apply_incremental(archive, temporary)
            else:
                updated = temporary / 'full'
                self._extract_resource_bundle(archive, updated)
            self._validate_resource_version(updated, version)
            staged = RESOURCE_ROOT.with_name(RESOURCE_ROOT.name + '.part')
            shutil.rmtree(staged, ignore_errors=True)
            shutil.move(str(updated), str(staged))
        self._replace_root(staged)

    def _sync_with_git(self, git, repository, branch):
        """用 git 同步并返回同步后的提交号：已有仓库就快进，否则浅克隆。

        已有仓库直接以远端版本覆盖本地改动与提交。快进用 reset --hard 到 FETCH_HEAD 而不是 pull，
        浅克隆历史下 merge 容易失败；clean 时排除 STATE_FILE，它是本地同步记录而不是仓库内容。
        新建副本先克隆到 .part 目录再整体替换，避免中途失败留下半个资源目录。
        """
        base = [git, *self.git_options()]
        root = str(RESOURCE_ROOT)
        if (RESOURCE_ROOT / '.git').is_dir():
            self._log(f'更新已有仓库（{branch}）…')
            self._run([*base, '-C', root, 'remote', 'set-url', 'origin', repository], timeout=30)
            self._run([*base, '-C', root, 'fetch', '--depth=1', 'origin', branch], timeout=1800)
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
        """没有 git 时下载 GitHub 分支压缩包替换本地副本；这条路径拿不到提交号，返回 None。

        解压前逐个检查成员路径，拒绝绝对路径和 `..`，否则压缩包可以写到资源目录之外。
        压缩包顶层是 `仓库名-分支` 目录，要剥掉一层；缺少 version.json 说明下载到的不是资源
        仓库（常见于把主项目地址填成了资源地址），此时报错而不是装上一个用不了的目录。
        临时目录开在 RESOURCE_ROOT 的同级，保证解压完是同盘移动而不是跨盘复制。
        """
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
        """流式下载到文件，并按 20MB 的步长报告进度。

        资源包有几百 MB，必须分块写盘而不是一次读进内存；进度按已下载量的增量报，不按次数报，
        否则日志缓冲会被刷满。压缩包不提供可靠的 Content-Length，所以只报已下载量不报百分比。
        """
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
        """把来源仓库、分支、提交和时间写进资源目录里的 .resource_state.json。

        必须在替换完目录之后写，文件要落在新目录里；version 顺带从新同步的 version.json 读一份，
        读不到就留空——状态文件只是辅助信息，不该因为它让整次同步失败。
        """
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
