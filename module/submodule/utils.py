import importlib
import os

from module.logger import logger

MOD_DICT = {

}
MOD_FUNC_DICT = {

}
MOD_CONFIG_DICT = {}


def get_available_func():
    return (
        'AutoTower',
        'SemiCombat',
        'Highlights',
        'BlaCDKManual',
        'ScreenRotate'
    )


def get_available_mod():
    return set(MOD_DICT)


def get_available_mod_func():
    return set(MOD_FUNC_DICT)


def get_func_mod(func):
    return MOD_FUNC_DICT.get(func)


def list_mod_dir():
    return list(MOD_DICT.items())


def get_mod_dir(name):
    return MOD_DICT.get(name)


def get_mod_filepath(name):
    return os.path.join('./submodule', get_mod_dir(name))


def list_mod_template():
    out = []
    for file in os.listdir('./config'):
        name, extension = os.path.splitext(file)
        config_name, mod_name = os.path.splitext(name)
        mod_name = mod_name[1:]
        if config_name == 'template' and extension == '.json' and mod_name != '':
            out.append(f'{config_name}-{mod_name}')

    return out


def list_mod_instance():
    global MOD_CONFIG_DICT
    MOD_CONFIG_DICT.clear()
    out = []
    for file in os.listdir('./config'):
        name, extension = os.path.splitext(file)
        config_name, mod_name = os.path.splitext(name)
        mod_name = mod_name[1:]
        if config_name != 'template' and extension == '.json' and mod_name != '':
            out.append(config_name)
            MOD_CONFIG_DICT[config_name] = mod_name

    return out


def get_config_mod(config_name):
    """
    Args:
        config_name (str):
    """
    if config_name.startswith('template-'):
        return config_name.replace('template-', '')
    try:
        return MOD_CONFIG_DICT[config_name]
    except KeyError:
        return 'nkas'

def load_mod(name):
    dir_name = get_mod_dir(name)
    if dir_name is None:
        logger.critical("No function matched")
        return

    return importlib.import_module('.' + name, 'submodule.' + dir_name)


def load_config(config_name):
    from module.config.config import NikkeConfig

    mod_name = get_config_mod(config_name)
    if mod_name == 'nkas':
        return NikkeConfig(config_name, '')
    else:
        config_lib = importlib.import_module(
            '.config',
            'submodule.' + get_mod_dir(mod_name) + '.module.config')
        return config_lib.load_config(config_name, '')
