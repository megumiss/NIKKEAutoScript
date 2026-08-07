import ctypes
import os
import pickle
import sys
from ctypes import wintypes
from functools import cached_property

from deploy.config import DeployConfig
from deploy.logger import logger
from deploy.utils import *

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


class NKASManager(DeployConfig):
    @cached_property
    def self_pid(self):
        return os.getpid()

    @staticmethod
    def normalize_executable_path(path):
        if not path:
            return ''
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    @staticmethod
    def snapshot_processes():
        """
        Fast process enumeration via Toolhelp32 snapshot (~10ms for hundreds
        of processes), used to avoid the multi-second WMI query at startup.

        Returns:
            list[tuple[int, str]] | None: (process_id, exe name) list,
            None if the snapshot is unavailable.
        """
        if not sys.platform.startswith('win'):
            return None
        try:
            kernel32 = ctypes.windll.kernel32
            snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snapshot == -1 or snapshot is None:
                return None
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                processes = []
                ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
                while ok:
                    processes.append((int(entry.th32ProcessID), entry.szExeFile))
                    ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
                return processes
            finally:
                kernel32.CloseHandle(snapshot)
        except Exception as e:
            logger.info(f'Process snapshot failed, fallback to WMI: {e}')
            return None

    @staticmethod
    def process_image_path(process_id):
        """
        Returns:
            str: Full executable path, '' if the process cannot be opened.
        """
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(process_id))
        if not handle:
            return ''
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return ''
        finally:
            kernel32.CloseHandle(handle)

    def iter_process_by_snapshot(self, name, allowed_paths, excluded_pids):
        """
        Snapshot-based variant of iter_process_by_name.

        Returns:
            list[tuple[str, str, int]] | None: (executable_path, process_name, process_id) list,
            None if the snapshot is unavailable and caller should fallback to WMI.
        """
        entries = self.snapshot_processes()
        if entries is None:
            return None
        name = name.lower()
        rows = []
        for process_id, process_name in entries:
            if not process_name or process_name.lower() != name:
                continue
            executable_path = self.process_image_path(process_id)
            if self.is_target_process(executable_path, process_id, allowed_paths, excluded_pids):
                rows.append((self.normalize_executable_path(executable_path), process_name, process_id))
        return rows

    @classmethod
    def is_target_process(cls, executable_path, process_id, allowed_paths, excluded_pids):
        try:
            process_id = int(process_id)
        except (TypeError, ValueError):
            return False
        if process_id in excluded_pids:
            return False
        executable_path = cls.normalize_executable_path(executable_path)
        return executable_path in {
            cls.normalize_executable_path(path) for path in allowed_paths if path
        }

    def iter_process_by_name(self, name, allowed_paths, excluded_pids=None):
        """
        Args:
            name (str): process name, such as 'nkas.exe'

        Yields:
            str, str, int: executable_path, process_name, process_id
        """
        excluded_pids = {self.self_pid, *(excluded_pids or set())}
        rows = self.iter_process_by_snapshot(name, allowed_paths, excluded_pids)
        if rows is not None:
            yield from rows
            return
        # Fallback: WMI enumeration, slow (~3s per query) but widely compatible
        for _ in range(2):
            try:
                from win32com.client import GetObject
            except ModuleNotFoundError:
                # No module named pywin32
                logger.info('pywin32 not installed, skip')
                return False
            except (pickle.UnpicklingError, EOFError) as e:
                # _pickle.UnpicklingError: invalid load key, '\x00'.
                # EOFError: Ran out of input
                logger.error(f'{type(e).__name__}: {e}')
                import sys

                import win32api
                # From win32com/client/__init__.py
                gen_path = os.path.join(win32api.GetTempPath(), "gen_py",
                                        "%d.%d" % (sys.version_info[0], sys.version_info[1]))
                # From win32com/client/gencache.py
                file = os.path.join(gen_path, "dicts.dat")
                # Try deleting it
                file = os.path.abspath(file).replace('\\', '/')
                if os.path.exists(file):
                    logger.info(f'win32com dicts.dat exists, removing: {file}')
                    os.remove(file)
                    continue
                else:
                    logger.warning(f'Cannot find win32com dicts.dat')
                    continue
        try:
            _ = GetObject
        except UnboundLocalError:
            logger.warning('Unable to import win32com.client, please fix it manually, '
                           'see https://github.com/LmeSzinc/AzurLaneAutoScript/issues/2382')
            exit(1)

        try:
            wmi = GetObject('winmgmts:')
            processes = wmi.InstancesOf('Win32_Process')
            for p in processes:
                executable_path = p.Properties_["ExecutablePath"].Value
                process_name = p.Properties_("Name").Value
                process_id = int(p.Properties_["ProcessID"].Value)

                if process_name != name:
                    continue
                if self.is_target_process(executable_path, process_id, allowed_paths, excluded_pids):
                    yield self.normalize_executable_path(executable_path), process_name, process_id
        except Exception as e:
            # Possible exception
            # pywintypes.com_error: (-2147217392, 'OLE error 0x80041010', None, None)
            logger.info(str(e))
            return False

    def kill_by_name(self, name, allowed_paths, excluded_pids=None):
        """
        Args:
            name (str): Process name
        """
        logger.hr(f'Kill {name}', 1)
        for row in self.iter_process_by_name(name, allowed_paths, excluded_pids):
            logger.info(' '.join(map(str, row)))
            self.execute(f'taskkill /f /pid {row[2]}', allow_failure=True, output=False)

    def nkas_kill(self, excluded_pids=None):
        logger.hr('Kill existing NKAS', 0)
        root_executable = os.path.join(self.root_filepath, 'nkas.exe')
        python_executable = self.filepath('PythonExecutable')
        self.kill_by_name('nkas.exe', {root_executable}, excluded_pids)
        self.kill_by_name(os.path.basename(python_executable), {python_executable}, excluded_pids)

if __name__ == '__main__':
    NKASManager().nkas_kill()
