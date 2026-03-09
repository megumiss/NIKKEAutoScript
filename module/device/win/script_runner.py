import os
import subprocess

from module.logger import logger


def run_script(script_path):
    """
    Run an external file on Windows.
    Supports: .exe, .ps1, .bat
    """
    if not isinstance(script_path, str) or not script_path.strip():
        logger.warning('Script path is empty')
        return False

    script_path = os.path.abspath(os.path.expanduser(script_path.strip()))
    if not os.path.exists(script_path):
        logger.warning(f'Script path does not exist: {script_path}')
        return False

    file_ext = os.path.splitext(script_path)[1].lower()
    if file_ext not in {'.ps1', '.bat', '.exe'}:
        logger.warning(f'Unsupported script type: {file_ext}')
        return False

    script_dir = os.path.dirname(script_path)
    original_cwd = os.getcwd()
    creation_flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)

    try:
        os.chdir(script_dir)
        if file_ext == '.ps1':
            subprocess.Popen(
                ['powershell', '-ExecutionPolicy', 'Bypass', '-File', script_path],
                creationflags=creation_flags,
            )
            logger.info(f'PowerShell script started: {script_path}')
        elif file_ext == '.bat':
            subprocess.Popen(
                [script_path],
                shell=True,
                creationflags=creation_flags,
            )
            logger.info(f'Batch script started: {script_path}')
        else:
            subprocess.Popen(
                [script_path],
                creationflags=creation_flags,
            )
            logger.info(f'Executable started: {script_path}')
        return True
    except Exception as e:
        logger.error(f'Failed to start script: {e}')
        return False
    finally:
        os.chdir(original_cwd)
