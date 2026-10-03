"""Validated file-level package installation with a recoverable journal."""

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from deploy.mirrorchyan import MirrorError, atomic_json, read_json

STATE = 'config/.update'
VERSION = 'app-version.json'
MANIFEST = 'release-files.json'
HISTORY = 'update-history.json'
METADATA = {VERSION, MANIFEST, HISTORY}


class UpdateLock:
    def __init__(self, root):
        self.path = Path(root) / STATE / 'install.lock'
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise MirrorError('另一个更新正在安装，请稍后重试') from None
        return self

    def __exit__(self, *_):
        if os.name == 'nt':
            import msvcrt

            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.stream.close()


def assert_ready(root):
    state = Path(root) / STATE
    if any((state / name).exists() for name in ('journal.json', 'desktop-pending.json', 'git-transition.json')):
        raise MirrorError('更新尚未完成，请先重试更新或重新启动程序完成恢复')


def recover_desktop(root):
    root = Path(root)
    pending = root / STATE / 'desktop-pending.json'
    state = read_json(pending)
    if state is None:
        return
    import psutil

    if any(psutil.pid_exists(pid) for pid in (state.get('pid'), state.get('helper_pid')) if isinstance(pid, int)):
        raise MirrorError('桌面更新仍在运行，请等待重启完成')
    previous, target_hash = state.get('previous_sha256'), state.get('target_sha256')
    if not previous or not target_hash:
        raise MirrorError('桌面更新恢复记录不完整，请检查 tmp/desktop-update 中的备份')
    target, backup = root / 'nkas.exe', root / 'tmp/desktop-update/nkas-current.exe.old'
    if target.is_file() and digest(target) in (previous, target_hash):
        pending.unlink()
        return
    if backup.is_file() and digest(backup) == previous:
        copy_atomic(backup, target)
        pending.unlink()
        return
    raise MirrorError('桌面更新未完成且备份校验失败，请重新安装启动器')


def safe_path(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise MirrorError('更新包包含无效路径')
    path = PurePosixPath(name.rstrip('/'))
    if path.is_absolute() or any(part in ('', '.', '..') for part in name.rstrip('/').split('/')):
        raise MirrorError('更新包路径越界')
    for part in path.parts:
        if part.endswith((' ', '.')) or re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', part):
            raise MirrorError('更新包包含无效 Windows 路径')
    return path.as_posix()


def owned_path(name):
    name = safe_path(name)
    low = name.lower()
    parts = low.split('/')
    if any(part in ('.git', '.security', '.update', '__pycache__', 'node_modules') for part in parts):
        return False
    if parts[0] in ('toolkit', 'log', 'tmp', 'data', 'output', 'pic', 'android', '.github', '.venv'):
        return False
    if low.endswith(('.acc', '.pyc')) or low == 'nkas.exe':
        return False
    if parts[0] == 'config':
        return low in (
            'config/notices.yaml',
            'config/warehouse_items.yaml',
            'config/template.json',
            'config/shortcuts.template.yaml',
        ) or (len(parts) == 2 and parts[1].startswith('deploy.template') and low.endswith('.yaml'))
    return True


def contained(root, name):
    name = safe_path(name)
    root = Path(root).resolve()
    path = root.joinpath(*name.split('/'))
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise MirrorError('更新路径包含符号链接或目录联接')
        if parent.exists() and getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400:
            raise MirrorError('更新路径包含 Windows 重解析点')
    if not path.resolve().is_relative_to(root):
        raise MirrorError('更新路径越界')
    return path


def extract(archive, destination):
    seen, total = set(), 0
    try:
        with zipfile.ZipFile(archive) as package:
            entries = package.infolist()
            if len(entries) > 100000:
                raise MirrorError('更新包文件过多')
            for entry in entries:
                name = safe_path(entry.filename)
                if name.lower() in seen:
                    raise MirrorError('更新包存在重复路径')
                seen.add(name.lower())
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise MirrorError('更新包包含链接或特殊文件')
                total += entry.file_size
                if total > 4 * 1024**3 or entry.file_size > 1024**3:
                    raise MirrorError('更新包展开大小超限')
                path = contained(destination, name)
                if entry.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as source, path.open('wb') as target:
                        shutil.copyfileobj(source, target, 64 * 1024)
    except (zipfile.BadZipFile, RuntimeError, EOFError) as error:
        if isinstance(error, MirrorError):
            raise
        raise MirrorError('更新 ZIP 无效或损坏') from None


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def manifest(root):
    value = read_json(Path(root) / MANIFEST, {})
    if not isinstance(value, dict) or not isinstance(value.get('files', {}), dict):
        raise MirrorError('发行文件清单无效')
    files = value.get('files', {})
    seen = set()
    for name, sha in files.items():
        if not owned_path(name) or name.lower() in seen or not re.fullmatch(r'[0-9a-f]{64}', str(sha)):
            raise MirrorError('发行文件清单包含无效路径或校验值')
        seen.add(name.lower())
    return files


def git_files(root, git='git'):
    try:
        result = subprocess.run([git, '-C', str(root), 'ls-files', '-z'], capture_output=True, check=True)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {name for name in result.stdout.decode('utf-8').split('\0') if name and owned_path(name)}


def copy_atomic(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, suffix='.update-tmp')
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def recover(root, dependencies):
    root = Path(root)
    journal_path = root / STATE / 'journal.json'
    journal = read_json(journal_path)
    if not journal:
        return
    if journal.get('phase') != 'committed':
        backup = root / STATE / 'backup'
        for name, existed in journal['files'].items():
            if not owned_path(name):
                raise MirrorError('恢复清单包含受保护路径')
            target = contained(root, name)
            if existed:
                copy_atomic(contained(backup, name), target)
            else:
                target.unlink(missing_ok=True)
        if journal.get('phase') == 'dependencies':
            dependencies()
    journal_path.unlink()
    shutil.rmtree(root / STATE / 'backup', ignore_errors=True)


def install_source(root, stage, info, dependencies, git='git'):
    root, stage = Path(root), Path(stage)
    if (root / '.git').is_file():
        raise MirrorError('开发 worktree 不支持覆盖安装，请在独立安装目录使用 Mirror 酱')
    version = read_json(stage / VERSION, {})
    if not isinstance(version, dict) or version.get('version') != info['version_name']:
        raise MirrorError('源码包版本与 Mirror 酱响应不一致')
    files = manifest(stage)
    if not files or not {VERSION, HISTORY}.issubset(files):
        raise MirrorError('源码包缺少版本、历史或发行清单')
    previous = manifest(root)
    old_files = set(previous) | (METADATA if previous else git_files(root, git))
    update_type = info.get('update_type')
    payload = {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
    if update_type == 'incremental':
        if not previous:
            raise MirrorError('增量包缺少本地发行清单，请重新检查获取全量包')
        changes = read_json(stage / 'changes.json')
        if not isinstance(changes, dict):
            raise MirrorError('增量包缺少 changes.json')
        lists = {}
        for field in ('added', 'modified', 'deleted', 'added_dir', 'deleted_dir'):
            values = changes.get(field, [])
            if not isinstance(values, list):
                raise MirrorError('增量变更清单格式错误')
            lists[field] = {safe_path(value) for value in values}
            if any(not owned_path(value) for value in lists[field]):
                raise MirrorError('增量变更涉及受保护文件')
        writes = lists['added'] | lists['modified']
        deletes = lists['deleted'] | {
            name for name in old_files if any(name.startswith(d + '/') for d in lists['deleted_dir'])
        }
        if payload != writes | {'changes.json'} or not deletes.issubset(old_files):
            raise MirrorError('增量包内容与变更清单不一致')
        if (old_files - deletes) | writes != set(files) | {MANIFEST}:
            raise MirrorError('增量包与本地安装基线不一致')
        for name in set(files) - writes:
            path = contained(root, name)
            if not path.is_file() or digest(path) != files[name]:
                raise MirrorError('本地文件与增量基线不一致，请重新获取全量更新')
    elif update_type == 'full':
        writes, deletes = set(files) | {MANIFEST}, old_files - set(files) - {MANIFEST}
        if payload != writes:
            raise MirrorError('全量包内容与发行清单不一致')
    else:
        raise MirrorError('Mirror 酱返回了不支持的更新包类型')
    for name in writes:
        if not owned_path(name):
            raise MirrorError('更新涉及受保护文件')
        target = contained(root, name)
        if target.exists() and name not in old_files:
            raise MirrorError(f'更新与本地非发行文件冲突：{name}')
        if target.exists() and not target.is_file():
            raise MirrorError(f'更新文件与本地目录冲突：{name}')
        if name in files and digest(contained(stage, name)) != files[name]:
            raise MirrorError('源码文件 SHA-256 校验失败')
    # The Git checkout is only a baseline for the first package install.
    if not previous and (root / '.git').is_dir():
        result = subprocess.run([git, '-C', str(root), 'diff', '--quiet', 'HEAD'], capture_output=True)
        if result.returncode:
            raise MirrorError('本地 Git 文件存在改动，请先处理后再启用 Mirror 酱')
    backup = root / STATE / 'backup'
    backup.mkdir(parents=True, exist_ok=True)
    journal = {'phase': 'files', 'files': {}}
    for name in sorted(writes | deletes):
        target = contained(root, name)
        journal['files'][name] = target.is_file()
        if target.is_file():
            copy_atomic(target, contained(backup, name))
    journal_path = root / STATE / 'journal.json'
    atomic_json(journal_path, journal)
    try:
        if update_type == 'incremental':
            for name in lists['added_dir']:
                contained(root, name).mkdir(parents=True, exist_ok=True)
        for name in sorted(deletes):
            contained(root, name).unlink(missing_ok=True)
        for name in sorted(writes - {VERSION}):
            copy_atomic(contained(stage, name), contained(root, name))
        journal['phase'] = 'dependencies'
        atomic_json(journal_path, journal)
        dependencies()
        copy_atomic(stage / VERSION, root / VERSION)
        journal['phase'] = 'committed'
        atomic_json(journal_path, journal)
    except Exception:
        # Keep the journal if dependency repair also fails: tasks must stay stopped.
        recover(root, dependencies)
        raise
    recover(root, dependencies)
    # Only remove empty obsolete directories: untracked user files survive.
    old_directories = {str(PurePosixPath(name).parent) for name in deletes}
    if update_type == 'incremental':
        old_directories.update(lists['deleted_dir'])
    for name in sorted(old_directories - {'.'}, key=lambda value: value.count('/'), reverse=True):
        try:
            contained(root, name).rmdir()
        except OSError:
            pass


def stage_desktop(client, info):
    if info.get('update_type') != 'full':
        raise MirrorError('桌面更新只支持全量 ZIP')
    directory = client.root / 'tmp/desktop-update'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        temporary = Path(temporary)
        archive = temporary / 'update.zip'
        stage = temporary / 'files'
        client.download(info, archive)
        extract(archive, stage)
        if {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()} != {
            'nkas.exe',
            'nkas-desktop.json',
        }:
            raise MirrorError('桌面包必须只包含 nkas.exe 和 nkas-desktop.json')
        metadata = read_json(stage / 'nkas-desktop.json', {})
        exe = stage / 'nkas.exe'
        if (
            not isinstance(metadata, dict)
            or metadata.get('schema') != 1
            or metadata.get('desktop_version') != info['version_name']
            or metadata.get('target') != 'x86_64-pc-windows-msvc'
            or metadata.get('size') != exe.stat().st_size
            or not 0 < exe.stat().st_size <= 30 * 1024**2
            or metadata.get('sha256') != digest(exe)
        ):
            raise MirrorError('桌面 EXE 的版本、架构、大小或 SHA-256 校验失败')
        # Version is supplied by a remote API; never use it as an unchecked path.
        target = directory / ('nkas-update-' + safe_path(info['version_name']) + '.exe')
        if target.parent != directory:
            raise MirrorError('桌面版本无效')
        copy_atomic(exe, target)
        return {'path': str(target.resolve()), 'sha256': metadata['sha256'], 'size': metadata['size']}
