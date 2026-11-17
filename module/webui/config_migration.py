import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import yaml

from deploy.atomic import to_tmp_file


LOCAL_DEPLOY_KEYS = {
    'PythonExecutable', 'GitExecutable', 'AdbExecutable', 'RequirementsFile', 'SSHExecutable',
    'WebuiHost', 'WebuiPort', 'Password', 'ConsoleEnabled', 'ConsoleAllowHosts',
}


class MigrationError(ValueError):
    def __init__(self, message, restart_required=False):
        super().__init__(message)
        self.restart_required = restart_required


def _linked(path):
    if not path.exists() and not path.is_symlink():
        return False
    attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    return path.is_symlink() or bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def _check_path(path, root):
    if not path.resolve().is_relative_to(root):
        raise MigrationError(f'路径超出部署目录：{path}')
    for part in (path, *path.parents):
        if _linked(part):
            raise MigrationError(f'不支持迁移符号链接或目录联接：{part}')
        if part != path and part.exists() and not part.is_dir():
            raise MigrationError(f'目标路径的父级不是文件夹：{part}')
        if part == root:
            break


def _excluded(relative):
    name = relative.name.lower()
    if any(part.lower() in ('log', 'logs', '.virtual_display') for part in relative.parts):
        return True
    if name in ('reloadnkas', 'reloadflag') or name.endswith(('.log', '.lock', '.tmp')) or '.log.' in name:
        return True
    # Bundled templates must match the current code, not the old deployment.
    return relative.parts[0] == 'config' and (
        name in ('template.json', 'notices.yaml') or '.template' in name or name.startswith(('.entry-', '.mirror-'))
    )


def _read_document(path):
    try:
        text = path.read_text(encoding='utf-8-sig')
        return json.loads(text) if path.suffix.lower() == '.json' else yaml.safe_load(text)
    except (UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise MigrationError(f'配置文件格式无效，请先修复再迁移：{path}') from exc


def _merge(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _local_settings(data, keep):
    result = {}
    for key, value in data.items():
        if key in LOCAL_DEPLOY_KEYS:
            if keep:
                result[key] = value
        elif isinstance(value, dict):
            children = _local_settings(value, keep)
            if children or not keep:
                result[key] = children
        elif not keep:
            result[key] = value
    return result


def _relocate_paths(data, source, destination, key=''):
    if isinstance(data, dict):
        return {name: _relocate_paths(value, source, destination, str(name)) for name, value in data.items()}
    if isinstance(data, list):
        return [_relocate_paths(value, source, destination, key) for value in data]
    if not isinstance(data, str) or not key.lower().endswith(('path', 'file', 'dir', 'directory', 'executable')):
        return data
    old = os.path.normcase(os.path.normpath(data))
    root = os.path.normcase(str(source))
    if old != root and not old.startswith(root + os.sep):
        return data
    relative = os.path.relpath(os.path.normpath(data), source)
    target = destination / relative
    return target.as_posix() if '/' in data and '\\' not in data else str(target)


def _prepare_file(relative, source, destination):
    if relative == Path('config/.security/entry.key'):
        from module.webui.security_entry import KEY_PATTERN
        try:
            value = (source / relative).read_text(encoding='ascii').strip()
        except UnicodeError as exc:
            raise MigrationError('旧部署安全入口密钥格式无效，请先修复再迁移。') from exc
        if not KEY_PATTERN.fullmatch(value):
            raise MigrationError('旧部署安全入口密钥格式无效，请先修复再迁移。')
        return None
    if relative.parts[0] != 'config' or relative.suffix.lower() not in ('.json', '.yaml', '.yml'):
        return None
    path = source / relative
    data = _read_document(path)
    migrated = _relocate_paths(data, source, destination)
    if relative == Path('config/deploy.yaml'):
        if not isinstance(data, dict) or not isinstance(data.get('Deploy'), dict):
            raise MigrationError('旧部署 deploy.yaml 中缺少有效的 Deploy 配置。')
        webui = data['Deploy'].get('Webui', {})
        if not isinstance(webui, dict):
            raise MigrationError('旧部署 Webui 配置格式无效。')
        if webui.get('SecurityEntryEnabled') is True:
            key_file = source / 'config/.security/entry.key'
            _check_path(key_file, source)
            if not key_file.is_file():
                raise MigrationError('旧部署已开启安全入口，但缺少 entry.key，请先修复再迁移。')
        local = {}
        for name in ('deploy.template.yaml', 'deploy.yaml'):
            current = destination / 'config' / name
            _check_path(current, destination)
            if not current.exists():
                continue
            document = _read_document(current)
            if not isinstance(document, dict):
                raise MigrationError(f'当前部署配置格式无效：{current}')
            _merge(local, _local_settings(document, keep=True))
        migrated = _local_settings(migrated, keep=False)
        _merge(migrated, local)
    elif relative == Path('config/serial/state.json'):
        if not isinstance(migrated, dict):
            raise MigrationError('旧部署串行状态文件格式无效。')
        # The token and heartbeat belong to processes in the old deployment.
        migrated.update(current=None, heartbeat=0, instances={})
    if migrated == data:
        return None
    if path.suffix.lower() == '.json':
        return (json.dumps(migrated, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return yaml.safe_dump(migrated, allow_unicode=True, sort_keys=False).encode('utf-8')


def plan_migration(source, destination):
    if not isinstance(source, str) or not source.strip():
        raise MigrationError('请选择旧部署目录。')
    raw_source = Path(source.strip()).expanduser()
    if not raw_source.is_absolute():
        raise MigrationError('旧部署目录必须是绝对路径。')
    source = raw_source.resolve(strict=True)
    _check_path(raw_source, source)
    destination = Path(destination).resolve(strict=True)
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise MigrationError('旧部署目录和当前部署目录不能相同或互相包含。')
    if not (source / 'config').is_dir():
        raise MigrationError('旧部署目录中未找到 config 文件夹，请选择部署根目录。')
    files = []
    adjusted = []
    ignored = 0
    size = 0
    signature = hashlib.sha256()
    signature.update(str(source).encode('utf-8'))
    for folder in ('config', 'data'):
        base = source / folder
        _check_path(base, source)
        if not base.exists():
            continue
        if not base.is_dir():
            raise MigrationError(f'不是文件夹：{base}')
        def walk_error(error):
            raise error
        for directory, dirs, names in os.walk(base, onerror=walk_error, followlinks=False):
            current = Path(directory)
            for name in list(dirs):
                path = current / name
                if _excluded(path.relative_to(source)):
                    ignored += 1
                    dirs.remove(name)
                    continue
                _check_path(path, source)
            for name in sorted(names):
                path = current / name
                relative = path.relative_to(source)
                if _excluded(relative):
                    ignored += 1
                    continue
                _check_path(path, source)
                target = destination / relative
                _check_path(target, destination)
                if not path.is_file() or (target.exists() and not target.is_file()):
                    raise MigrationError(f'迁移文件类型冲突：{relative}')
                prepared = _prepare_file(relative, source, destination)
                if prepared is not None:
                    adjusted.append(str(relative))
                    signature.update(prepared)
                info = path.stat()
                target_info = target.stat() if target.exists() else None
                files.append(relative)
                size += info.st_size
                signature.update(json.dumps([
                    str(relative), info.st_size, info.st_mtime_ns,
                    (target_info.st_size, target_info.st_mtime_ns) if target_info else None,
                ]).encode('utf-8'))
            dirs.sort()
    if not files:
        raise MigrationError('旧部署目录中没有可迁移的配置或持久化文件。')
    conflicts = [str(path) for path in files if (destination / path).exists()]
    return {
        'source': str(source), 'destination': str(destination), 'files': files,
        'file_count': len(files), 'bytes': size, 'conflict_count': len(conflicts),
        'conflicts': conflicts, 'adjusted_files': adjusted,
        'ignored_count': ignored, 'token': signature.hexdigest(),
    }


def migrate_files(plan):
    source = Path(plan['source'])
    destination = Path(plan['destination'])
    if plan_migration(str(source), destination)['token'] != plan['token']:
        raise MigrationError('文件在准备迁移时发生变化，请退出旧部署后重新预览。')
    prepared = {relative: _prepare_file(relative, source, destination) for relative in plan['files']}
    copied = 0
    try:
        for relative in plan['files']:
            old = source / relative
            target = destination / relative
            _check_path(old, source)
            _check_path(target, destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(to_tmp_file(str(target)))
            try:
                content = prepared[relative]
                if content is None:
                    shutil.copy2(old, temporary)
                else:
                    temporary.write_bytes(content)
                # Keep the old target intact if writing the new file fails.
                os.replace(temporary, target)
                copied += 1
            finally:
                temporary.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        raise MigrationError(
            f'迁移失败，已完成 {copied} 个文件。'
            + ('请重启程序后重新迁移：' if copied else '请排除错误后重新预览：') + str(exc),
            restart_required=copied > 0,
        ) from exc
