"""游戏多开：复制游戏安装目录，重命名启动器，并写入新副本的启动器配置。

启动器按 exe 名在 %APPDATA% 下读取各自的配置目录（如 nikke_launcher2.exe 对应
%APPDATA%\\nikke_launcher2），其中的 production_gl_launcher.db（TEA 加密）记录了
download_path / game_resource_path，需要改写为新副本的路径。游戏本体 nikke.exe
保持原名：启动器按固定文件名拉起游戏，改名后副本无法启动。
"""

import os
import re
import shutil
import sqlite3
import struct
import tempfile
import threading

import psutil

from module.logger import logger
from module.tools.oicrypt import oi_decrypt, oi_encrypt

# 与 AppControl.check_path_format 一致，启动器/游戏路径必须是纯英文路径
ASCII_PATH = re.compile(r'^[A-Za-z0-9_:/\\.\- ()]+$')
CLIENT_PATTERN = re.compile(r'^(nikke_launcher(?:_hmt)?)(\d*)$', re.IGNORECASE)


class GameCloneError(Exception):
    """用户可读的校验/执行错误，消息直接展示在前端。"""


_state = {
    'running': False, 'step': '', 'total': 0, 'copied': 0, 'error': '', 'result': None,
}
_state_lock = threading.Lock()
_cancel_event = threading.Event()


def _set_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)


def clone_status():
    with _state_lock:
        return dict(_state)


def _appdata_dir():
    return os.path.expandvars(r'%APPDATA%')


def _read_launcher_setting(db_path, key):
    db = None
    try:
        db = sqlite3.connect(db_path)
        row = db.execute('SELECT data FROM Setting WHERE key=?', (key,)).fetchone()
        if not row or row[0] is None:
            return ''
        cipher = row[0].tobytes() if isinstance(row[0], memoryview) else bytes(row[0])
        value = oi_decrypt(cipher).decode('utf-8').strip().strip('"')
        return os.path.normpath(value) if value else ''
    except (OSError, sqlite3.Error, struct.error, TypeError, ValueError, UnicodeError) as exc:
        logger.warning(f'读取启动器配置失败 [{db_path}] {key}: {exc}')
        return ''
    finally:
        if db is not None:
            db.close()


def _game_executable(install_path):
    if not install_path:
        return ''
    game_dir = os.path.join(install_path, 'NIKKE', 'game')
    expected = os.path.join(game_dir, 'nikke.exe')
    if os.path.isfile(expected):
        return expected
    try:
        candidates = sorted(
            name for name in os.listdir(game_dir)
            if re.fullmatch(r'nikke.*\.exe', name, re.IGNORECASE)
            and os.path.isfile(os.path.join(game_dir, name))
        )
    except OSError:
        return expected
    return os.path.join(game_dir, candidates[0]) if candidates else expected


def _scan_client(appdata_dir, name, match):
    config_path = os.path.join(appdata_dir, name)
    db_path = os.path.join(config_path, 'production_gl_launcher.db')
    install_path = _read_launcher_setting(db_path, 'download_path') if os.path.isfile(db_path) else ''
    launcher_path = os.path.join(install_path, 'Launcher', f'{name}.exe') if install_path else ''
    game_path = _game_executable(install_path)
    launcher_exists = bool(launcher_path) and os.path.isfile(launcher_path)
    game_exists = bool(game_path) and os.path.isfile(game_path)
    if not install_path:
        status = 'invalid'
    elif launcher_exists and game_exists:
        status = 'ready'
    elif os.path.isdir(install_path):
        status = 'incomplete'
    else:
        status = 'missing'
    region = 'hmt' if match.group(1).lower().endswith('_hmt') else 'intl'
    suffix = match.group(2)
    return {
        'name': name, 'region': region, 'suffix': suffix, 'is_clone': bool(suffix),
        'config_path': config_path, 'install_path': install_path,
        'launcher_path': launcher_path, 'game_path': game_path, 'status': status,
    }


def clone_info():
    """扫描启动器配置目录，返回客户端路径和文件完整性状态，并推算下一个可用编号。"""
    clones = []
    clients = []
    used = set()
    appdata_dir = _appdata_dir()
    try:
        names = os.listdir(appdata_dir)
    except OSError as exc:
        logger.warning(f'扫描启动器配置目录失败 [{appdata_dir}]: {exc}')
        names = []
    for name in names:
        match = CLIENT_PATTERN.match(name)
        if match and os.path.isdir(os.path.join(appdata_dir, name)):
            clones.append(name)
            used.add(int(match.group(2)) if match.group(2) else 1)
            clients.append(_scan_client(appdata_dir, name, match))
    suffix = 2
    while suffix in used:
        suffix += 1
    clients.sort(
        key=lambda item: (
            0 if item['region'] == 'intl' else 1,
            bool(item['suffix']),
            int(item['suffix'] or 0),
            item['name'].lower(),
        )
    )
    # 共用同一安装目录的客户端不能整目录删除，标记出来供前端提示并改走仅删配置
    by_install = {}
    for client in clients:
        if client['install_path']:
            key = os.path.normcase(os.path.normpath(client['install_path']))
            by_install.setdefault(key, []).append(client['name'])
    for client in clients:
        if client['install_path']:
            key = os.path.normcase(os.path.normpath(client['install_path']))
            client['shared_with'] = sorted(n for n in by_install[key] if n != client['name'])
        else:
            client['shared_with'] = []
    return {'clones': sorted(clones), 'clients': clients, 'next_suffix': suffix}


def delete_clone(name, mode='full'):
    with _state_lock:
        if _state['running']:
            raise GameCloneError('复制任务正在进行，无法删除副本')

    name = str(name or '').strip()
    match = CLIENT_PATTERN.fullmatch(name)
    if not match or not match.group(2):
        raise GameCloneError('只能删除带编号的游戏副本')
    if mode not in ('full', 'config'):
        raise GameCloneError(f'未知删除模式: {mode}')

    appdata_dir = _appdata_dir()
    config_path = os.path.join(appdata_dir, name)
    if not os.path.isdir(config_path) or os.path.islink(config_path):
        raise GameCloneError(f'副本配置不存在: {config_path}')
    client = _scan_client(appdata_dir, name, match)
    install_path = client['install_path']

    sharers = []
    if install_path:
        normalized_install = os.path.normcase(os.path.normpath(install_path))
        sharers = sorted(
            other['name'] for other in clone_info()['clients']
            if other['name'] != name and other['install_path'] and os.path.normcase(
                os.path.normpath(other['install_path'])
            ) == normalized_install
        )
    if sharers and mode == 'full':
        raise GameCloneError(
            f'安装目录被其他配置共用，无法删除: {install_path} ({"、".join(sharers)})。'
            f'可先删除共用配置，或选择仅删除当前副本的配置'
        )

    if mode == 'full' and install_path:
        _check_no_running(install_path)
        if os.path.exists(install_path):
            if not os.path.isdir(install_path) or os.path.islink(install_path):
                raise GameCloneError(f'副本安装路径不是安全的目录: {install_path}')
            if not (
                os.path.isdir(os.path.join(install_path, 'Launcher'))
                or os.path.isdir(os.path.join(install_path, 'NIKKE'))
            ):
                raise GameCloneError(f'无法确认副本安装目录，未删除: {install_path}')
            try:
                shutil.rmtree(install_path)
            except OSError as exc:
                raise GameCloneError(f'删除副本安装目录失败: {exc}') from exc

    try:
        shutil.rmtree(config_path)
    except OSError as exc:
        raise GameCloneError(f'删除副本配置失败: {exc}') from exc
    logger.info(f'Game clone deleted: {name}, mode={mode}, install={install_path or "<missing>"}')
    return {'name': name, 'install_path': install_path}


def cancel_clone():
    with _state_lock:
        if not _state['running']:
            raise GameCloneError('当前没有正在进行的复制任务')
        _cancel_event.set()
        _state['step'] = '正在取消'


def _find_launcher(src_root):
    """
    在安装目录的 Launcher 文件夹中找出实际使用的启动器。
    目录里可能同时存在原始启动器和多开副本（如 nikke_launcher.exe 与
    nikke_launcher2.exe），优先选 AppData 配置中 download_path 指向当前
    安装目录的那个，其次是有配置目录的，最后取名字最短的。
    """
    launcher_dir = os.path.join(src_root, 'Launcher')
    if not os.path.isdir(launcher_dir):
        raise GameCloneError(f'未找到启动器目录: {launcher_dir}')
    candidates = sorted(
        (name for name in os.listdir(launcher_dir) if re.match(r'nikke_launcher.*\.exe$', name, re.IGNORECASE)),
        key=len,
    )
    if not candidates:
        raise GameCloneError(f'未找到启动器程序: {launcher_dir}')

    def appdata_of(name):
        return os.path.join(_appdata_dir(), os.path.splitext(name)[0])

    # 按配置里的 download_path 精确匹配
    for name in candidates:
        db_path = os.path.join(appdata_of(name), 'production_gl_launcher.db')
        if not os.path.isfile(db_path):
            continue
        try:
            with sqlite3.connect(db_path) as db:
                row = db.execute("SELECT data FROM Setting WHERE key='download_path'").fetchone()
            if row and os.path.normpath(oi_decrypt(row[0]).decode('utf-8')) == os.path.normpath(src_root):
                return name
        except Exception:
            continue
    for name in candidates:
        if os.path.isdir(appdata_of(name)):
            return name
    return candidates[0]


def _validate(source, target, suffix):
    raw = {'游戏安装目录': str(source or '').strip().strip('"'), '副本安装目录': str(target or '').strip().strip('"')}
    for label, path in raw.items():
        if not path:
            raise GameCloneError(f'请填写{label}')
        if not ASCII_PATH.match(path):
            raise GameCloneError(f'{label}必须是纯英文路径: {path}')
    source = os.path.normpath(raw['游戏安装目录'])
    target = os.path.normpath(raw['副本安装目录'])
    suffix = str(suffix or '').strip()

    if not os.path.isdir(source):
        raise GameCloneError(f'游戏安装目录不存在: {source}')
    src_root = source
    src_launcher_name = _find_launcher(src_root)

    game_dir = os.path.join(src_root, 'NIKKE', 'game')
    if not os.path.isdir(game_dir):
        raise GameCloneError(f'未找到游戏目录: {game_dir}')
    game_exes = sorted(
        (name for name in os.listdir(game_dir) if re.match(r'nikke.*\.exe$', name, re.IGNORECASE)),
        key=len,
    )
    if not game_exes:
        raise GameCloneError(f'未找到游戏程序: {game_dir}')

    if not re.fullmatch(r'\d+', suffix):
        raise GameCloneError('副本编号必须是数字')
    src_stem = re.sub(r'\d+$', '', os.path.splitext(src_launcher_name)[0])
    new_launcher_name = f'{src_stem}{suffix}.exe'
    new_appdata = os.path.join(_appdata_dir(), os.path.splitext(new_launcher_name)[0])
    if os.path.normcase(os.path.normpath(new_appdata)) == os.path.normcase(
        os.path.normpath(os.path.join(_appdata_dir(), os.path.splitext(src_launcher_name)[0]))
    ):
        raise GameCloneError('副本编号不能与源启动器编号相同')
    if os.path.exists(new_appdata) and not os.path.isdir(new_appdata):
        raise GameCloneError(f'编号 {suffix} 的配置路径不是目录: {new_appdata}')

    if os.path.exists(target) and (not os.path.isdir(target) or os.listdir(target)):
        raise GameCloneError(f'副本安装目录已存在且不为空: {target}')
    src_root_l = src_root.lower() + os.sep
    target_l = target.lower() + os.sep
    if target_l.startswith(src_root_l) or src_root_l.startswith(target_l):
        raise GameCloneError('副本安装目录不能与源目录互相包含')

    return {
        'src_root': src_root, 'src_launcher_name': src_launcher_name,
        'src_game_name': game_exes[0], 'target': target,
        'new_launcher_name': new_launcher_name,
        'new_appdata': new_appdata, 'appdata_exists': os.path.isdir(new_appdata),
        'target_exists': os.path.isdir(target),
    }


def _check_no_running(src_root):
    for proc in psutil.process_iter(attrs=['exe']):
        try:
            exe = proc.info['exe']
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if exe and os.path.normpath(exe).lower().startswith(src_root.lower() + os.sep):
            raise GameCloneError(f'源目录下有正在运行的进程，请先关闭: {os.path.basename(exe)}')


def _copy_file(source, target):
    with open(source, 'rb') as src, open(target, 'wb') as dst:
        while True:
            _check_cancel()
            chunk = src.read(4 * 1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
    shutil.copystat(source, target)


def _run_clone(params):
    try:
        src_root, target = params['src_root'], params['target']
        _check_no_running(src_root)

        target_parent = os.path.dirname(target) or os.curdir
        os.makedirs(target_parent, exist_ok=True)
        work_target = tempfile.mkdtemp(prefix=f'.{os.path.basename(target)}.nkas-copying-', dir=target_parent)
        params['work_target'] = work_target

        _set_state(step='统计文件')
        total = 0
        for dirpath, _, filenames in os.walk(src_root):
            _check_cancel()
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
        _set_state(total=total)

        _set_state(step='复制文件')
        copied = 0
        for dirpath, _, filenames in os.walk(src_root):
            _check_cancel()
            rel = os.path.relpath(dirpath, src_root)
            dst_dir = work_target if rel == '.' else os.path.join(work_target, rel)
            os.makedirs(dst_dir, exist_ok=True)
            for name in filenames:
                _check_cancel()
                src_file = os.path.join(dirpath, name)
                _copy_file(src_file, os.path.join(dst_dir, name))
                try:
                    copied += os.path.getsize(src_file)
                except OSError:
                    pass
                _set_state(copied=copied)

        _set_state(step='重命名启动器')
        _check_cancel()
        os.rename(
            os.path.join(work_target, 'Launcher', params['src_launcher_name']),
            os.path.join(work_target, 'Launcher', params['new_launcher_name']),
        )

        _check_cancel()
        if os.path.isdir(target):
            os.rmdir(target)
        os.replace(work_target, target)
        params['work_target'] = None
        params['target_created'] = True

        _set_state(step='写入配置')
        _check_cancel()
        src_appdata = os.path.join(_appdata_dir(), os.path.splitext(params['src_launcher_name'])[0])
        new_appdata = params['new_appdata']
        params['appdata_touched'] = True
        if os.path.isdir(src_appdata):
            # tbs_cache 是启动器内置浏览器缓存，体大且运行时有文件锁，启动时会重新生成
            shutil.copytree(
                src_appdata, new_appdata, dirs_exist_ok=True, ignore=shutil.ignore_patterns('tbs_cache')
            )
        else:
            os.makedirs(new_appdata, exist_ok=True)
        db_path = os.path.join(new_appdata, 'production_gl_launcher.db')
        with sqlite3.connect(db_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS Setting(key varchar(128),data blob,PRIMARY KEY(key))')
            entries = {
                'download_path': target + '\\',
                'game_resource_path': os.path.join(target, 'Unity', 'com_proximabeta_NIKKE'),
            }
            for key, value in entries.items():
                db.execute(
                    'INSERT OR REPLACE INTO Setting(key, data) VALUES (?, ?)',
                    (key, oi_encrypt(value.encode('utf-8'))),
                )
            db.commit()

        result = {
            'launcher': os.path.join(target, 'Launcher', params['new_launcher_name']),
            'game': os.path.join(target, 'NIKKE', 'game', params['src_game_name']),
        }
        logger.info(f'Game clone done: {result}')
        _set_state(running=False, step='', error='', result=result)
    except GameCloneError as e:
        _cleanup_on_error(params)
        _set_state(running=False, step='', error=str(e))
    except Exception as e:
        logger.exception(e)
        _cleanup_on_error(params)
        _set_state(running=False, step='', error=f'{type(e).__name__}: {e}')


def _check_cancel():
    if _cancel_event.is_set():
        raise GameCloneError('复制已取消')


def _cleanup_on_error(params):
    work_target = params.get('work_target')
    if work_target and os.path.isdir(work_target):
        shutil.rmtree(work_target, ignore_errors=True)

    new_appdata = params.get('new_appdata')
    if (
        params.get('appdata_touched')
        and not params.get('appdata_exists')
        and new_appdata
        and os.path.isdir(new_appdata)
    ):
        shutil.rmtree(new_appdata, ignore_errors=True)

    target = params.get('target')
    if params.get('target_created') and target and os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
        if params.get('target_exists'):
            os.makedirs(target, exist_ok=True)


def start_clone(source, target, suffix):
    with _state_lock:
        if _state['running']:
            raise GameCloneError('已有复制任务正在进行')
        params = _validate(source, target, suffix)
        _cancel_event.clear()
        _state.update({
            'running': True, 'step': '准备', 'total': 0, 'copied': 0, 'error': '', 'result': None,
        })
    threading.Thread(target=_run_clone, args=(params,), daemon=True).start()
    return params
