import os
import shutil
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
    is_windows = os.name == 'nt'
    if file_ext not in {'.ps1', '.bat', '.exe', '.sh'}:
        logger.warning(f'Unsupported script type: {file_ext}')
        return False

    creation_flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
    popen_kwargs = {'env': {**os.environ, 'NKAS_PID': str(os.getpid())}}
    if is_windows:
        popen_kwargs['creationflags'] = creation_flags

    try:
        if file_ext == '.ps1':
            if is_windows:
                subprocess.Popen(
                    ['powershell', '-ExecutionPolicy', 'Bypass', '-File', script_path],
                    **popen_kwargs,
                )
                logger.info(f'PowerShell script started: {script_path}')
            else:
                pwsh = shutil.which('pwsh')
                if not pwsh:
                    logger.warning('PowerShell (pwsh) not found for .ps1 on this platform')
                    return False
                subprocess.Popen([pwsh, '-File', script_path], **popen_kwargs)
                logger.info(f'PowerShell script started: {script_path}')
        elif file_ext == '.bat':
            if not is_windows:
                logger.warning('Batch script is only supported on Windows')
                return False
            subprocess.Popen([script_path], shell=True, **popen_kwargs)
            logger.info(f'Batch script started: {script_path}')
        elif file_ext == '.sh':
            bash = shutil.which('bash')
            if not bash:
                logger.warning('bash not found for .sh script')
                return False
            subprocess.Popen([bash, script_path], **popen_kwargs)
            logger.info(f'Shell script started: {script_path}')
        else:
            if not is_windows:
                logger.warning('Executable file is only supported on Windows')
                return False
            subprocess.Popen([script_path], **popen_kwargs)
            logger.info(f'Executable started: {script_path}')
        return True
    except Exception as e:
        logger.error(f'Failed to start script: {e}')
        return False
