server = 'cn'

VALID_SERVER = ['cn', 'tw']

VALID_PACKAGE = {
    'com.proximabeta.nikke': 'cn',
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

    from module.base.resource import release_resources
    release_resources()


def to_server(package_or_server: str) -> str:
    """
    Convert package/server to server.
    To unknown packages, consider they are a CN channel servers.
    """
    if package_or_server in VALID_SERVER:
        return package_or_server
    elif package_or_server in VALID_PACKAGE:
        return VALID_PACKAGE[package_or_server]
    elif package_or_server in VALID_CHANNEL_PACKAGE:
        return VALID_CHANNEL_PACKAGE[package_or_server][0]
    else:
        return 'cn'