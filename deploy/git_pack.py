import base64
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from deploy.mirrorchyan import MirrorError, SOURCE_RID, atomic_json, download, latest, read_cdk
from deploy.update_lock import UpdateLock
from module.logger import logger

OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
PACKAGE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
MAX_EXPANDED_SIZE = 1024 * 1024 * 1024


class GitPackUpdater:
    def __init__(self, manager):
        self.manager = manager
        self.root = Path(manager.root_filepath).resolve()
        self.directory = self.root / 'tmp' / 'mirror-source'
        self.journal = self.directory / 'transaction.json'
        self.lease = UpdateLock(self.root)
        self.lock = self.lease.path
        if not self.directory.resolve().is_relative_to(self.root):
            raise MirrorError('Mirror源码工作目录越界。')

    def git(self, *args, input=None, check=True, stdout=None):
        try:
            result = subprocess.run(
                [self.manager.git, *args], cwd=self.root, input=input, stdout=stdout or subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=180,
            )
        except FileNotFoundError:
            raise MirrorError(f'找不到 Git 可执行文件：{self.manager.git}。请检查 GitExecutable 配置。') from None
        except PermissionError:
            raise MirrorError(f'无法执行 Git：{self.manager.git}。请检查文件权限或安全软件拦截。') from None
        except subprocess.TimeoutExpired:
            command = args[0] if args else ''
            raise MirrorError(f'Mirror源码 Git 命令 {command} 超时（180 秒）。') from None
        except OSError as error:
            code = getattr(error, 'winerror', None) or error.errno
            raise MirrorError(f'无法启动 Git：{self.manager.git}（系统错误码 {code}）。') from None
        if check and result.returncode:
            detail = result.stderr.decode('utf-8', errors='replace').strip()[:500]
            raise MirrorError(f'Mirror源码 Git 操作失败：{detail}')
        return result

    def text(self, *args):
        return self.git(*args).stdout.decode('utf-8', errors='replace').strip()

    def version(self, revision='HEAD'):
        raw = self.text('cat-file', 'commit', revision).split('\n\n', 1)[0]
        try:
            committer = next(line for line in raw.splitlines() if line.startswith('committer '))
            stamp = int(committer.rsplit(' ', 2)[1])
            return datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y%m%d%H%M%S')
        except (StopIteration, ValueError, OverflowError, OSError):
            raise MirrorError('无法读取源码提交的 UTC committer 时间。') from None

    def eligible(self):
        if self.manager.Branch != 'master':
            raise MirrorError('Mirror源码目前仅支持官方 master 分支，请切换 Git 更新源。')

    def check(self):
        self.eligible()
        version = self.version()
        data = latest(SOURCE_RID, version, read_cdk(self.manager.file))
        self.validate_version(data['version_name'])
        return version, data

    @staticmethod
    def validate_version(value):
        try:
            if not re.fullmatch(r'[0-9]{14}', value):
                raise ValueError
            datetime.strptime(value, '%Y%m%d%H%M%S')
        except (ValueError, TypeError):
            raise MirrorError('Mirror源码返回了无效的 UTC 版本。') from None

    def acquire(self):
        self.lease.acquire()

    def release(self):
        self.lease.release()

    @staticmethod
    def safe_path(name):
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or '\\' in name or ':' in name
                or any(part in ('', '.', '..') or part.lower() == '.git' for part in name.split('/'))):
            raise MirrorError('Mirror源码包包含越界或非法路径。')
        if os.name == 'nt':
            reserved = {'con', 'prn', 'aux', 'nul', *[f'com{i}' for i in range(1, 10)],
                        *[f'lpt{i}' for i in range(1, 10)]}
            if any(part.rstrip(' .') != part or part.split('.')[0].lower() in reserved
                   or any(char in '<>\"|?*' or ord(char) < 32 for char in part) for part in path.parts):
                raise MirrorError('Mirror源码包含 Windows 不支持的路径。')
        return path

    def unpack(self, archive, directory):
        try:
            with zipfile.ZipFile(archive) as zipped:
                entries = zipped.infolist()
                names = [entry.filename for entry in entries]
                if len(entries) != 3 or len(set(names)) != 3:
                    raise MirrorError('Mirror源码 ZIP 必须包含 pack、idx 和 meta.json。')
                for entry in entries:
                    self.safe_path(entry.filename)
                    if '/' in entry.filename or entry.is_dir() or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise MirrorError('Mirror源码 ZIP 包含非法条目。')
                packs = [name for name in names if re.fullmatch(r'pack-(?:[0-9a-f]{40}|[0-9a-f]{64})\.pack', name)]
                if (len(packs) != 1 or packs[0][:-5] + '.idx' not in names or 'meta.json' not in names
                        or sum(entry.file_size for entry in entries) > MAX_EXPANDED_SIZE
                        or zipped.getinfo('meta.json').file_size > 64 * 1024):
                    raise MirrorError('Mirror源码 ZIP 元数据或体积无效。')
                meta = json.loads(zipped.read('meta.json'))
                if (not isinstance(meta, dict) or not isinstance(meta.get('commit'), str)
                        or not OID.fullmatch(meta['commit']) or not isinstance(meta.get('base'), str)
                        or (meta['base'] and not OID.fullmatch(meta['base']))):
                    raise MirrorError('Mirror源码 commit / base 对象 ID 无效。')
                target = directory / packs[0]
                with zipped.open(packs[0]) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output, 128 * 1024)
                return meta, target
        except (zipfile.BadZipFile, KeyError, ValueError, OSError, RuntimeError, NotImplementedError):
            raise MirrorError('Mirror源码 ZIP 损坏，已拒绝安装。') from None

    def verify_target(self, commit, version):
        objects = self.git('rev-list', '--objects', '--no-walk', commit).stdout
        ids = b'\n'.join(line.split(b' ', 1)[0] for line in objects.splitlines()) + b'\n'
        checked = self.git('cat-file', '--batch-check', input=ids).stdout
        if b' missing' in checked or b' dangling' in checked:
            raise MirrorError('Mirror源码缺少目标对象，请重新获取完整快照。')
        if self.version(commit) != version:
            raise MirrorError('Mirror源码目标提交与 API 版本不一致，已拒绝安装。')

    def shallow_path(self):
        path = Path(self.text('rev-parse', '--git-path', 'shallow'))
        return path if path.is_absolute() else self.root / path

    def maintain_shallow(self, commit):
        header = self.text('cat-file', 'commit', commit).split('\n\n', 1)[0]
        parents = [line.split()[1] for line in header.splitlines() if line.startswith('parent ')]
        if not any(self.git('cat-file', '-e', parent + '^{commit}', check=False).returncode for parent in parents):
            return
        path = self.shallow_path()
        values = set(path.read_text(encoding='ascii').splitlines()) if path.exists() else set()
        values.add(commit)
        self.atomic_bytes(path, ('\n'.join(sorted(values)) + '\n').encode('ascii'))

    @staticmethod
    def atomic_bytes(path, data):
        fd, temporary = tempfile.mkstemp(prefix='.mirror-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def preflight(self, commit, directory):
        old = set(self.git('ls-files', '-z').stdout.decode('utf-8').rstrip('\0').split('\0'))
        tree = self.git('ls-tree', '-rz', '--full-tree', commit).stdout
        new_files = {}
        for row in tree.split(b'\0'):
            if not row:
                continue
            header, raw_name = row.split(b'\t', 1)
            name = raw_name.decode('utf-8')
            self.safe_path(name)
            mode = header.split()[0]
            if mode not in (b'100644', b'100755'):
                raise MirrorError('Mirror源码目前不支持符号链接或子模块。')
            if name.split('/')[0].lower() in ('tmp', 'log', 'toolkit') or name.lower().startswith('config/.security/'):
                raise MirrorError('Mirror源码不能覆盖运行时或私有数据。')
            path = self.root / name
            if name not in old and (path.exists() or path.is_symlink()):
                raise MirrorError(f'Mirror源码与未跟踪文件冲突：{name}')
            if name not in old:
                new_files[name] = header.split()[2].decode('ascii')
            for parent in path.parents:
                if parent == self.root:
                    break
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise MirrorError(f'Mirror源码路径与现有文件冲突：{name}')
        archive = directory / 'source.tar'
        with archive.open('wb') as stream:
            self.git('archive', '--format=tar', commit, stdout=stream)
        staging = directory / 'source'
        staging.mkdir()
        total = 0
        with tarfile.open(archive) as tar:
            for entry in tar:
                self.safe_path(entry.name.rstrip('/'))
                total += entry.size
                if total > MAX_EXPANDED_SIZE or not (entry.isdir() or entry.isfile()):
                    raise MirrorError('Mirror源码快照条目或体积无效。')
                path = staging / entry.name
                if entry.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(entry) as source, path.open('wb') as output:
                        shutil.copyfileobj(source, output)
        try:
            result = subprocess.run(
                [self.manager.python, '-m', 'compileall', '-q', str(staging)],
                capture_output=True, timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise MirrorError('无法使用当前 Python 检查目标源码，请更新完整运行包。') from None
        if result.returncode:
            raise MirrorError('目标源码与当前 Python 不兼容，请更新完整运行包。')
        return new_files

    def packages(self):
        try:
            result = subprocess.run(
                [self.manager.python, '-m', 'pip', 'list', '--format=json', '--disable-pip-version-check'],
                capture_output=True, timeout=120, check=True,
            )
            rows = json.loads(result.stdout)
            return {row['name'].lower().replace('_', '-'): row['version'] for row in rows}
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            raise MirrorError('无法记录 Python 依赖快照，Mirror源码更新已取消。') from None

    def restore_packages(self, before):
        current = self.packages()
        install = [f'{name}=={version}' for name, version in before.items() if current.get(name) != version]
        remove = sorted(set(current) - set(before))
        args = ['--disable-pip-version-check']
        if self.manager.PypiMirror:
            args += ['-i', self.manager.PypiMirror]
            if urlparse(self.manager.PypiMirror).scheme == 'http' or not self.manager.SSLVerify:
                args += ['--trusted-host', urlparse(self.manager.PypiMirror).hostname]
        elif not self.manager.SSLVerify:
            args += ['--trusted-host', 'pypi.org', '--trusted-host', 'files.pythonhosted.org']
        for action in (['install', *install, *args] if install else [], ['uninstall', '-y', *remove] if remove else []):
            if not action:
                continue
            try:
                subprocess.run([self.manager.python, '-m', 'pip', *action], cwd=self.root,
                               check=True, capture_output=True, timeout=600)
            except (OSError, subprocess.SubprocessError):
                raise MirrorError('旧版 Python 依赖恢复失败，请停止任务并使用完整运行包恢复。') from None
        if self.packages() != before:
            raise MirrorError('Python 依赖未恢复到旧版快照，请使用完整运行包恢复。')

    def apply(self):
        self.eligible()
        if self.journal.exists():
            raise MirrorError('上次 Mirror源码更新尚未确认，请先重启程序完成恢复。')
        if self.text('status', '--porcelain', '-uno'):
            raise MirrorError('源码存在本地修改，Mirror更新已取消；请先提交或备份修改。')
        before = self.text('rev-parse', 'HEAD')
        version, data = self.check()
        if version == data['version_name']:
            logger.info('Mirror source is already up to date')
            return False
        self.directory.mkdir(parents=True, exist_ok=True)
        self.acquire()
        temporary = tempfile.TemporaryDirectory(prefix='pack-', dir=self.directory)
        staging = Path(temporary.name).resolve()
        try:
            # The temporary tree is the only recursively removed directory.
            if not staging.is_relative_to(self.directory.resolve()):
                raise MirrorError('Mirror源码临时目录越界。')
            for attempt in range(2):
                archive = download(data, staging / 'download.zip')
                meta, pack = self.unpack(archive, staging)
                if self.text('rev-parse', 'HEAD') != before:
                    raise MirrorError('下载期间 HEAD 已改变，更新已取消。')
                if meta['base'] and meta['base'] != before:
                    if attempt:
                        raise MirrorError('Mirror源码完整快照仍包含不匹配的基准。')
                    data = latest(SOURCE_RID, cdk=read_cdk(self.manager.file))
                    self.validate_version(data['version_name'])
                    continue
                with pack.open('rb') as stream:
                    self.git('index-pack', '--fsck-objects', '--stdin', input=stream.read())
                try:
                    self.verify_target(meta['commit'], data['version_name'])
                except MirrorError:
                    if attempt or not meta['base']:
                        raise
                    data = latest(SOURCE_RID, cdk=read_cdk(self.manager.file))
                    continue
                break
            new_files = self.preflight(meta['commit'], staging)
            if self.text('rev-parse', 'HEAD') != before or self.text('status', '--porcelain', '-uno'):
                raise MirrorError('应用前源码已改变，Mirror更新已取消。')
            shallow = self.shallow_path()
            old_ref = self.git('rev-parse', '--verify', 'refs/remotes/origin/master', check=False)
            record = {
                'schema': 1, 'old_head': before, 'target': meta['commit'], 'phase': 'prepared', 'launches': 0,
                'old_remote': old_ref.stdout.decode().strip() if not old_ref.returncode else '',
                'old_shallow': base64.b64encode(shallow.read_bytes()).decode() if shallow.exists() else None,
                'packages': self.packages() if self.manager.InstallDependencies else None,
                'new_files': new_files,
            }
            atomic_json(self.journal, record)
            self.maintain_shallow(meta['commit'])
            self.git('update-ref', 'refs/remotes/origin/master', meta['commit'])
            record['phase'] = 'checkout'
            atomic_json(self.journal, record)
            self.git('reset', '--hard', meta['commit'])
            if self.text('rev-parse', 'HEAD') != meta['commit'] or self.text('status', '--porcelain', '-uno'):
                raise MirrorError('Mirror源码检出后的 HEAD 或工作区不一致。')
            record['phase'] = 'dependencies'
            atomic_json(self.journal, record)
            logger.info(f"Mirror source checked out: {before[:8]} -> {meta['commit'][:8]}")
            return True
        finally:
            if staging.is_relative_to(self.directory.resolve()):
                temporary.cleanup()
            if not self.journal.exists():
                self.release()

    def record(self):
        try:
            record = json.loads(self.journal.read_text(encoding='utf-8'))
            if not isinstance(record, dict):
                raise ValueError
            if (record.get('schema') != 1 or not OID.fullmatch(record['old_head'])
                    or not OID.fullmatch(record['target'])
                    or record['phase'] not in ('prepared', 'checkout', 'dependencies', 'ready')
                    or (record.get('old_remote') and not OID.fullmatch(record['old_remote']))):
                raise ValueError
            new_files = record.get('new_files', {})
            if not isinstance(new_files, dict):
                raise ValueError
            for name, oid in new_files.items():
                self.safe_path(name)
                if not isinstance(oid, str) or not OID.fullmatch(oid):
                    raise ValueError
            packages = record.get('packages')
            if packages is not None and (not isinstance(packages, dict) or any(
                    not PACKAGE.fullmatch(name) or not isinstance(version, str)
                    or not re.fullmatch(r'[A-Za-z0-9.!+_-]+', version) for name, version in packages.items())):
                raise ValueError
            if not isinstance(record.get('launches'), int) or not 0 <= record['launches'] <= 1:
                raise ValueError
            if record['old_shallow'] is not None:
                for oid in base64.b64decode(record['old_shallow'], validate=True).decode('ascii').splitlines():
                    if not OID.fullmatch(oid):
                        raise ValueError
            return record
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            raise MirrorError('Mirror源码恢复记录损坏，请使用完整运行包恢复。') from None

    def ready(self):
        if not self.journal.exists():
            self.release()
            return
        record = self.record()
        if self.text('rev-parse', 'HEAD') != record['target'] or self.text('status', '--porcelain', '-uno'):
            raise MirrorError('依赖同步后源码已改变，Mirror更新未完成。')
        record['phase'] = 'ready'
        atomic_json(self.journal, record)
        self.release()

    def rollback(self):
        if not self.journal.exists():
            self.release()
            return False
        self.acquire()
        record = self.record()
        if self.text('rev-parse', 'HEAD') not in (record['old_head'], record['target']):
            raise MirrorError('更新后 HEAD 被其他操作改变，已停止自动恢复以保护本地提交。')
        if record['phase'] in ('dependencies', 'ready') and self.text('status', '--porcelain', '-uno'):
            raise MirrorError('更新后出现本地修改，已停止自动恢复以保护文件。')
        logger.warning('Restoring the previous Mirror source and dependencies')
        self.git('reset', '--hard', record['old_head'])
        for name, oid in record.get('new_files', {}).items():
            self.safe_path(name)
            path = self.root / name
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != self.root):
                raise MirrorError('恢复时出现符号链接，已停止恢复以保护用户文件。')
            if not path.resolve().is_relative_to(self.root):
                raise MirrorError('恢复文件路径越界，已停止恢复。')
            if not path.exists():
                continue
            if not path.is_file() or self.text('hash-object', str(path)) != oid:
                raise MirrorError(f'恢复时发现新的用户文件，已保留：{name}')
            path.unlink()
        if record['old_remote']:
            self.git('update-ref', 'refs/remotes/origin/master', record['old_remote'])
        else:
            self.git('update-ref', '-d', 'refs/remotes/origin/master')
        shallow = self.shallow_path()
        old_shallow = base64.b64decode(record['old_shallow'], validate=True) if record['old_shallow'] else b''
        if old_shallow:
            self.atomic_bytes(shallow, old_shallow)
        else:
            shallow.unlink(missing_ok=True)
        # Imported snapshot objects remain in the ODB after rollback. Their
        # missing-parent boundary must remain too, otherwise git fsck breaks.
        self.maintain_shallow(record['target'])
        if record['packages'] is not None:
            self.restore_packages(record['packages'])
        if self.text('rev-parse', 'HEAD') != record['old_head'] or self.text('status', '--porcelain', '-uno'):
            raise MirrorError('旧版源码恢复校验失败，请停止任务并使用完整运行包恢复。')
        self.journal.unlink()
        self.release()
        logger.info('Previous source and dependencies restored')
        return True

    def prepare_launch(self):
        if not self.journal.exists():
            return
        self.acquire()
        try:
            record = self.record()
            if record['phase'] != 'ready' or record['launches'] >= 1:
                self.rollback()
                return
            if self.text('rev-parse', 'HEAD') != record['target']:
                raise MirrorError('待确认的 Mirror源码 HEAD 已改变，请检查恢复记录。')
            record['launches'] += 1
            atomic_json(self.journal, record)
        finally:
            self.release()

    def acknowledge(self):
        if not self.journal.exists():
            return
        record = self.record()
        if record['phase'] == 'ready' and self.text('rev-parse', 'HEAD') == record['target']:
            self.journal.unlink()
            logger.info('Mirror source startup confirmed')
