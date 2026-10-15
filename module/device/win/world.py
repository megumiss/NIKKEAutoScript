import datetime
import json
import os
import winreg

from module.logger import logger

PREFS_KEY = r'Software\com.proximabeta\NIKKE'
VALUE_PREFIX = 'variantsJson'
BACKUP_DIR = 'tmp'

# 港澳台客户端（com.gamamobi.nikke）只有一个世界，不在可选范围内
GAME_WORLDS = {
    'jp': 'pub:live-jp',
    'na': 'pub:live-na',
    'kr': 'pub:live-kr',
    'global': 'pub:live-global',
    'sea': 'pub:live-sea',
}


def _read_variants():
    """读取注册表中的 variantsJson 值，返回 (值名, 原始字节, dict)；读不到返回 None。"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, PREFS_KEY)
    except OSError:
        return None
    with key:
        for i in range(winreg.QueryInfoKey(key)[1]):
            name, _, _ = winreg.EnumValue(key, i)
            if name.startswith(VALUE_PREFIX):
                data, _ = winreg.QueryValueEx(key, name)
                if not isinstance(data, bytes):
                    return None
                return name, data, json.loads(data.decode('utf-8').rstrip('\x00'))
    return None


def set_game_world(world) -> bool:
    """启动本体前把国际服客户端的所属区服改写为指定区服，返回是否生效。

    由调用方保证游戏本体未运行（本体在登录/退出时会重写该值）；
    客户端从未登录过时注册表还没有该值，无法预设区服。
    """
    world_name = GAME_WORLDS.get(world)
    if not world_name:
        logger.warning(f'Unknown game world: {world}')
        return False
    try:
        variants = _read_variants()
    except (OSError, ValueError) as e:
        logger.warning(f'Unable to read game world setting: {e}')
        return False
    if variants is None:
        logger.warning('Game world setting not found in registry, launch the game once before selecting a world')
        return False

    name, data, obj = variants
    old = obj.get('LastSelectedWorldName')
    if old == world_name:
        logger.info(f'Game world is already {world_name}')
        return True

    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = os.path.join(BACKUP_DIR, f'nikke_world_backup_{stamp}.bin')
    try:
        with open(backup, 'wb') as file:
            file.write(data)
    except OSError as e:
        logger.warning(f'Unable to backup game world setting: {e}')
        return False

    obj['LastSelectedWorldName'] = world_name
    payload = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\x00'
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, PREFS_KEY, 0, winreg.KEY_WRITE)
        with key:
            winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, payload)
    except OSError as e:
        logger.warning(f'Unable to write game world setting: {e}')
        return False

    variants = _read_variants()
    if variants and variants[2].get('LastSelectedWorldName') == world_name:
        logger.info(f'Game world set: {old} -> {world_name}')
        return True
    logger.warning(f'Game world verification failed, expected {world_name}')
    return False
