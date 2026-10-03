"""One channel decision for startup, scheduled, GUI and batch updates."""

import json
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from deploy.config import ExecutionError
from deploy.mirrorchyan import SOURCE_RID, Client, MirrorError, atomic_json, enabled, read_json
from deploy.update_package import (
    HISTORY,
    METADATA,
    STATE,
    VERSION,
    UpdateLock,
    assert_ready,
    contained,
    digest,
    extract,
    git_files,
    install_source,
    manifest,
    recover,
    recover_desktop,
)


class DependencyError(ExecutionError, MirrorError):
    pass


def current_version(root, git='git'):
    metadata = read_json(Path(root) / VERSION, {})
    if not isinstance(metadata, dict):
        raise MirrorError('本地源码版本记录损坏')
    if metadata.get('version'):
        return metadata['version']
    try:
        return subprocess.check_output(
            [git, '-C', str(root), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True, timeout=10
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def check_source(root, git='git', client=None, require_url=True):
    client = client or Client(root)
    current = current_version(root, git)
    # Only a verified installed manifest is a valid file-level delta baseline.
    installed = manifest(root)
    baseline = (
        current
        if installed
        and all(
            contained(root, name).is_file() and digest(contained(root, name)) == sha for name, sha in installed.items()
        )
        else ''
    )
    info = client.latest(SOURCE_RID, baseline)
    available = info['version_name'] != current or bool(installed) and not baseline
    if available and require_url:
        client.require_download(info)
    return info, available


def refresh_history(root, version):
    cache = Path(root) / STATE / 'history.json'
    try:
        url = f'https://nkas.megumiss.top/releases/history/{version}.json'
        with urllib.request.urlopen(url, timeout=10) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                return False
            value = json.loads(raw)
        if not isinstance(value, dict) or value.get('version') != version or not isinstance(value.get('commits'), list):
            return False
        atomic_json(cache, value)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def history(root):
    root = Path(root)
    for path in (root / STATE / 'history.json', root / HISTORY):
        try:
            data = read_json(path, {})
            if isinstance(data.get('commits'), list):
                return data['commits']
        except (OSError, ValueError, AttributeError):
            continue
    return []


def run_update(manager, progress=None, client=None):
    root = Path(manager.root_filepath)
    mirror = enabled(root)

    def dependencies():
        try:
            manager.pip_install()
        except Exception as error:
            raise DependencyError(str(error)) from None

    with UpdateLock(root):
        recover_desktop(root)
        if (root / STATE / 'desktop-pending.json').exists():
            raise MirrorError('桌面更新正在等待重启，请稍后重试')
        recover(root, dependencies)
        transition = root / STATE / 'git-transition.json'
        if mirror and transition.exists():
            raise MirrorError('上次切回 Git 的更新尚未完成，请手动关闭 Mirror 酱后重试修复')
        before = current_version(root, manager.git)
        if mirror:
            client = client or Client(root)
            info, available = check_source(root, manager.git, client)
            if not available:
                return False
            with tempfile.TemporaryDirectory(prefix='nkas-mirror-') as directory:
                directory = Path(directory)
                archive, stage = directory / 'source.zip', directory / 'files'
                client.download(info, archive, progress)
                extract(archive, stage)
                install_source(root, stage, info, dependencies, manager.git)
            after = info['version_name']
        else:
            previous = manifest(root)
            # Explicitly switching back to Git reconciles package-owned files too.
            if previous and (root / '.git').is_file():
                raise MirrorError('开发 worktree 中的包版本记录需要手动处理')
            if previous:
                atomic_json(transition, {'from': before})
            manager.git_update()
            dependencies()
            if previous:
                tracked = git_files(root, manager.git)
                if not tracked:
                    raise MirrorError('无法读取 Git 发行文件清单，请保持原渠道并重试更新')
                for name in (set(previous) | METADATA) - tracked:
                    contained(root, name).unlink(missing_ok=True)
            transition.unlink(missing_ok=True)
            after = current_version(root, manager.git)
        atomic_json(
            root / STATE / 'last-update.json',
            {
                'from': before,
                'version': after,
                'channel': 'MirrorChyan' if mirror else 'Git',
                'installed_at': datetime.now(timezone.utc).isoformat(),
            },
        )
        assert_ready(root)
        return before != after
