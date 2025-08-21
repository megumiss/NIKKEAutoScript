server = 'intl'

VALID_SERVER = ['intl', 'tw']

VALID_PACKAGE = {
    'com.proximabeta.nikke': 'intl',
    'com.gamamobi.nikke': 'tw',
}

DICT_PACKAGE_TO_ACTIVITY = {
    'com.proximabeta.nikke': 'com.shiftup.nk.MainActivity',
}

VALID_CHANNEL_PACKAGE = {

}

def set_server(package_or_server: str):
    """
    Change server and this will effect globally,
    including assets and server specific methods.

    Args:
        package_or_server: package name or server.
    """
    global server
    server = to_server(package_or_server)


def to_server(package_or_server: str) -> str:
    """
    Convert package/server to server.
    To unknown packages, consider they are a intl channel servers.
    """
    if package_or_server in VALID_SERVER:
        return package_or_server
    elif package_or_server in VALID_PACKAGE:
        return VALID_PACKAGE[package_or_server]
    elif package_or_server in VALID_CHANNEL_PACKAGE:
        return VALID_CHANNEL_PACKAGE[package_or_server][0]
    else:
        return 'intl'
