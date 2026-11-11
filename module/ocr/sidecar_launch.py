"""
Build the OCR sidecar launch command for NKAS running in Termux + proot-distro
on the Android device it controls.

Everything is derived, nothing is configured:
- ADB binary and serial come from the instance's own device connection
  (`set_adb_target`), so the command targets the device NKAS already controls.
- The repository's host path is the Termux home directory whose inode matches
  the repository NKAS runs from (proot bind mounts keep inodes).
- The proot-distro name is the container whose rootfs is our `/`.
"""
import os

from module.logger import logger

TERMUX_PACKAGE = 'com.termux'
TERMUX_HOME = '/data/data/com.termux/files/home'
PROOT_CONTAINERS = '/data/data/com.termux/files/usr/var/lib/proot-distro/containers'
SIDECAR_SCRIPT = 'deploy/termux/ocr_sidecar.sh'
# Repositories are cloned directly into the Termux home or one level below it.
SEARCH_DEPTH = 2

_adb_target = None


def set_adb_target(adb_binary, serial):
    """Called by the ADB device once connected; the sidecar is launched through it."""
    global _adb_target
    _adb_target = (str(adb_binary), str(serial))


def _same_dir(a, b):
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def find_host_repo(repo='.'):
    """Return the Termux-side path of `repo`, or None when not running from the Termux home."""
    if not os.path.isdir(TERMUX_HOME):
        return None
    pending = [(TERMUX_HOME, 0)]
    while pending:
        path, depth = pending.pop(0)
        try:
            entries = sorted((e for e in os.scandir(path) if e.is_dir(follow_symlinks=False)), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith('.'):
                continue
            if os.path.isfile(os.path.join(entry.path, SIDECAR_SCRIPT)) and _same_dir(entry.path, repo):
                return entry.path
            if depth + 1 < SEARCH_DEPTH:
                pending.append((entry.path, depth + 1))
    return None


def find_proot_distro():
    """Return the proot-distro container name whose rootfs is this process's `/`."""
    try:
        names = sorted(os.listdir(PROOT_CONTAINERS))
    except OSError:
        return None
    for name in names:
        if _same_dir(os.path.join(PROOT_CONTAINERS, name, 'rootfs'), '/'):
            return name
    return None


def termux_sidecar_command():
    """
    Returns:
        tuple[list[str] | None, str]: argv, or None with the reason it is unavailable.
    """
    if _adb_target is None:
        return None, 'no ADB physical device connected in this process'
    repo = find_host_repo()
    if repo is None:
        return None, f'NKAS is not running from a repository under {TERMUX_HOME}'
    distro = find_proot_distro()
    if distro is None:
        return None, 'NKAS is not running inside a Termux proot-distro container'
    adb, serial = _adb_target
    command = [adb, '-s', serial, 'shell', 'run-as', TERMUX_PACKAGE, 'sh', f'{repo}/{SIDECAR_SCRIPT}', distro]
    logger.info(f'OCR sidecar command: {" ".join(command)}')
    return command, ''
