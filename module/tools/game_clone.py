"""游戏多开：复制游戏安装目录，重命名启动器与游戏进程，并写入新副本的启动器配置。

启动器按 exe 名在 %APPDATA% 下读取各自的配置目录（如 nikke_launcher2.exe 对应
%APPDATA%\\nikke_launcher2），其中的 production_gl_launcher.db（TEA 加密）记录了
download_path / game_resource_path，需要改写为新副本的路径。
"""

import os
import re
import shutil
import sqlite3
import threading

import psutil

from module.logger import logger
from module.tools.oicrypt import oi_decrypt, oi_encrypt

# 与 AppControl.check_path_format 一致，启动器/游戏路径必须是纯英文路径
ASCII_PATH = re.compile(r'^[A-Za-z0-9_:/\\.\- ()]+$')


class GameCloneError(Exception):
    """用户可读的校验/执行错误，消息直接展示在前端。"""


_state = {
    'running': False, 'step': '', 'total': 0, 'copied': 0, 'error': '', 'result': None,
}
_state_lock = threading.Lock()


def _set_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)


def clone_status():
    with _state_lock:
        return dict(_state)


def _appdata_dir():
    return os.path.expandvars(r'%APPDATA%')


def clone_info():
    """扫描 %APPDATA% 下已有的启动器配置目录，推算下一个可用编号。"""
    clones = []
    used = set()
    pattern = re.compile(r'^(nikke_launcher(?:_hmt)?)(\d*)$')
    for name in os.listdir(_appdata_dir()):
        match = pattern.match(name)
        if match and os.path.isdir(os.path.join(_appdata_dir(), name)):
            clones.append(name)
            used.add(int(match.group(2)) if match.group(2) else 1)
    suffix = 2
    while suffix in used:
        suffix += 1
    return {'clones': sorted(clones), 'next_suffix': suffix}


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
    new_game_name = f'{re.sub(r"[0-9]+$", "", os.path.splitext(game_exes[0])[0])}{suffix}.exe'
    new_appdata = os.path.join(_appdata_dir(), os.path.splitext(new_launcher_name)[0])
    if os.path.exists(new_appdata):
        raise GameCloneError(f'编号 {suffix} 的配置目录已存在: {new_appdata}')

    if os.path.exists(target) and (not os.path.isdir(target) or os.listdir(target)):
        raise GameCloneError(f'副本安装目录已存在且不为空: {target}')
    src_root_l = src_root.lower() + os.sep
    target_l = target.lower() + os.sep
    if target_l.startswith(src_root_l) or src_root_l.startswith(target_l):
        raise GameCloneError('副本安装目录不能与源目录互相包含')

    return {
        'src_root': src_root, 'src_launcher_name': src_launcher_name,
        'src_game_name': game_exes[0], 'target': target,
        'new_launcher_name': new_launcher_name, 'new_game_name': new_game_name,
        'new_appdata': new_appdata,
    }


def _check_no_running(src_root):
    for proc in psutil.process_iter(attrs=['exe']):
        try:
            exe = proc.info['exe']
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if exe and os.path.normpath(exe).lower().startswith(src_root.lower() + os.sep):
            raise GameCloneError(f'源目录下有正在运行的进程，请先关闭: {os.path.basename(exe)}')


def _run_clone(params):
    try:
        src_root, target = params['src_root'], params['target']
        _check_no_running(src_root)

        _set_state(step='统计文件')
        total = 0
        for dirpath, _, filenames in os.walk(src_root):
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
        _set_state(total=total)

        _set_state(step='复制文件')
        copied = 0
        for dirpath, _, filenames in os.walk(src_root):
            rel = os.path.relpath(dirpath, src_root)
            dst_dir = target if rel == '.' else os.path.join(target, rel)
            os.makedirs(dst_dir, exist_ok=True)
            for name in filenames:
                src_file = os.path.join(dirpath, name)
                shutil.copy2(src_file, os.path.join(dst_dir, name))
                try:
                    copied += os.path.getsize(src_file)
                except OSError:
                    pass
                _set_state(copied=copied)

        _set_state(step='重命名程序')
        new_launcher = os.path.join(target, 'Launcher', params['new_launcher_name'])
        os.rename(os.path.join(target, 'Launcher', params['src_launcher_name']), new_launcher)
        new_game = os.path.join(target, 'NIKKE', 'game', params['new_game_name'])
        os.rename(os.path.join(target, 'NIKKE', 'game', params['src_game_name']), new_game)

        _set_state(step='写入配置')
        src_appdata = os.path.join(_appdata_dir(), os.path.splitext(params['src_launcher_name'])[0])
        new_appdata = params['new_appdata']
        if os.path.isdir(src_appdata):
            # tbs_cache 是启动器内置浏览器缓存，体大且运行时有文件锁，启动时会重新生成
            shutil.copytree(src_appdata, new_appdata, ignore=shutil.ignore_patterns('tbs_cache'))
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

        result = {'launcher': new_launcher, 'game': new_game}
        logger.info(f'Game clone done: {result}')
        _set_state(running=False, step='', error='', result=result)
    except GameCloneError as e:
        _cleanup_on_error(params)
        _set_state(running=False, step='', error=str(e))
    except Exception as e:
        logger.exception(e)
        _cleanup_on_error(params)
        _set_state(running=False, step='', error=f'{type(e).__name__}: {e}')


def _cleanup_on_error(params):
    # 失败时删掉刚创建的配置目录，避免残留导致重试时报"编号已存在"；
    # 该目录在校验阶段确认不存在，可安全删除。目标目录可能已有大量文件，留给用户处理。
    new_appdata = params.get('new_appdata')
    if new_appdata and os.path.isdir(new_appdata):
        shutil.rmtree(new_appdata, ignore_errors=True)


def start_clone(source, target, suffix):
    with _state_lock:
        if _state['running']:
            raise GameCloneError('已有复制任务正在进行')
        params = _validate(source, target, suffix)
        _state.update({
            'running': True, 'step': '准备', 'total': 0, 'copied': 0, 'error': '', 'result': None,
        })
    threading.Thread(target=_run_clone, args=(params,), daemon=True).start()
    return params
