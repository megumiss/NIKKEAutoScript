import os

MOD_CONFIG_DICT = {}

def mod_instance():
    global MOD_CONFIG_DICT
    MOD_CONFIG_DICT.clear()
    out = []
    for file in os.listdir('./config'):
        name, extension = os.path.splitext(file)
        '''
            mod_name应该为alas，Daemon，AzurLaneUncensored，Benchmark，GameManager，maa，MaaCopilot
            例如: maa1.maa.json
            config_name为maa1，mod_name为maa
        '''
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