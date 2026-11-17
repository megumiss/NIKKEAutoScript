import datetime
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Generator, List, Tuple

import requests
from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.mirrorchyan import MirrorError
from deploy.pip import PipManager
from deploy.utils import DEPLOY_CONFIG
from module.base.retry import retry
from module.logger import logger
from module.webui.config import DeployConfig
from module.webui.process_manager import ProcessManager
from module.webui.setting import State
from module.webui.utils import TaskHandler, get_next_time


def _decode_output(data: bytes) -> str:
    """Decode subprocess stdout tolerantly.

    On Windows, git emits CJK commit messages in the ANSI code page (GBK),
    so strict UTF-8 decoding (e.g. under PEP 540 UTF-8 mode) raises
    UnicodeDecodeError in the reader thread.  Try UTF-8 first, fall back to
    GBK, then give up with replacement chars rather than crashing.
    """
    if not data:
        return ''
    for encoding in ('utf-8', 'gbk'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


class Updater(DeployConfig, GitManager, PipManager):
    def __init__(self, file=DEPLOY_CONFIG):
        super().__init__(file=file)
        self.state = 0
        self.event: threading.Event = None
        self._check_started_at = 0.0
        # Human-readable reason of the last failed update check, surfaced to
        # the SPA via /api/system/update so a failure is not silently shown
        # as "up to date".
        self.check_error = ''
        self.update_error = ''
        self.source_info = {}
        self._operation_lock = threading.Lock()

    @property
    def delay(self):
        self.read()
        return int(self.CheckUpdateInterval) * 60

    @property
    def schedule_time(self):
        self.read()
        t = self.AutoRestartTime
        if t is None:
            return None
        try:
            return datetime.time.fromisoformat(t)
        except ValueError:
            # Python < 3.11 的 fromisoformat 不接受省略前导零的格式（如 '3:50'）。
            # 此前解析失败会让 schedule_update 任务异常并被 TaskHandler 移除，
            # 导致定时重启更新静默失效（手动更新不受影响）。这里兜底解析 HH:MM[:SS]。
            try:
                parts = t.split(':')
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
                second = int(parts[2]) if len(parts) > 2 else 0
                return datetime.time(hour=hour, minute=minute, second=second)
            except (ValueError, TypeError, IndexError):
                logger.warning(f'Invalid AutoRestartTime: {t!r}, scheduled restart disabled')
                return None

    def execute_output(self, command, cwd=None) -> str:
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        result = subprocess.run(command, capture_output=True, shell=True, cwd=cwd)
        return _decode_output(result.stdout)

    def get_commit(self, revision="", n=1, short_sha1=False, cwd=None) -> Tuple:
        """
        Return:
            (sha1, author, isotime, message,)
        """
        ph = "h" if short_sha1 else "H"

        log = self.execute_output(
            f'"{self.git}" log {revision} --pretty=format:"%{ph}---%an---%ad---%s" --date=iso -{n}', cwd=cwd
        )

        if not log:
            return None, None, None, None

        logs = log.split("\n")
        logs = list(map(lambda log: tuple(log.split("---")), logs))

        if n == 1:
            return logs[0]
        else:
            return logs

    @property
    def history_directory(self):
        return Path(self.root_filepath) / 'tmp' / 'git-history'

    def refresh_git_history(self):
        directory = self.history_directory
        directory.mkdir(parents=True, exist_ok=True)
        remote = self.execute_output(f'"{self.git}" remote get-url origin', cwd=self.root_filepath).strip()
        if not remote:
            return False
        if not (directory / 'HEAD').is_file():
            subprocess.run([self.git, 'init', '--bare', str(directory)],
                           capture_output=True, check=True, timeout=10)
        subprocess.run([self.git, '-C', str(directory), 'config', 'remote.origin.url', remote],
                       capture_output=True, check=True, timeout=10)
        # Keep partial Git objects separate so Mirror updates never lazily download source files from Git.
        return self._git_fetch('origin', depth=20, timeout=20, cwd=directory, filter_spec='tree:0')

    def _git_fetch(self, source, depth=None, timeout=120, cwd=None, filter_spec=None) -> bool:
        command = [self.git, 'fetch']
        if depth:
            command.append(f'--depth={depth}')
        if filter_spec:
            command.append(f'--filter={filter_spec}')
        command.extend([source, f'+refs/heads/{self.Branch}:refs/remotes/{source}/{self.Branch}'])
        logger.info(subprocess.list2cmdline(command))
        try:
            with subprocess.Popen(command, cwd=cwd or self.root_filepath, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, start_new_session=os.name != 'nt') as process:
                try:
                    process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # Killing only the launcher leaves Git children holding the captured pipes open on Windows.
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                       capture_output=True, timeout=10)
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.kill()
                    process.communicate(timeout=5)
                    logger.warning(f'Git fetch timed out after {timeout}s')
                    return False
                if process.returncode:
                    logger.info(f'[ allowed failure ], error_code: {process.returncode}')
                    return False
        except OSError as error:
            logger.warning(f'Unable to fetch Git history ({type(error).__name__})')
            return False
        logger.info('[ success ]')
        return True

    def _check_update(self) -> bool:
        self.state = "checking"
        self.check_error = ''
        # Any unexpected failure must not leave the state machine in
        # "checking" forever; it would block every later check and update.
        # Distinguish failure (state "failed") from a clean "no update"
        # (state 0/False) so the UI can surface the error instead of
        # reporting "up to date".
        try:
            return self._do_check_update()
        except Exception as e:
            if isinstance(e, MirrorError):
                self.check_error = str(e)
                logger.warning(self.check_error)
            elif self.UpdateSource == 'mirrorchyan':
                # The project formatter captures frame locals. Mirror HTTP
                # frames contain the CDK and signed URL, so never log them.
                self.check_error = f'Mirror源码检查异常（{type(e).__name__}），请检查配置或稍后重试。'
                logger.warning(self.check_error)
            else:
                logger.exception(e)
                logger.warning("Check update failed")
                self.check_error = str(e) or e.__class__.__name__
            self.state = "failed"
            return False

    def _do_check_update(self) -> bool:
        self.read()
        self.source_info = {}
        if self.UpdateSource == 'mirrorchyan':
            local, data = self.mirror_updater().check()
            self.source_info = {
                'source': 'mirrorchyan', 'local_version': local, 'remote_version': data['version_name'],
                'release_note': str(data.get('release_note') or ''), 'needs_cdk': not bool(data.get('url')),
            }
            available = local != data['version_name']
            logger.info(f"Mirror source: {local} -> {data['version_name']}, update available: {available}")
            try:
                fetched = self.refresh_git_history()
            except (OSError, subprocess.SubprocessError) as error:
                logger.warning(f'Unable to refresh Git history ({type(error).__name__})')
                fetched = False
            if not fetched:
                logger.warning('Git history refresh failed; keep cached history and the Mirror update result')
            return available
        if State.deploy_config.GitOverCdn:
            status = self.goc_client.get_status()
            if status == "uptodate":
                logger.info(f"No update")
                return False
            elif status == "behind":
                logger.info(f"New update available")
                return True
            else:
                # failed, should fallback to `git pull`
                pass

        source = "origin"
        for _ in range(3):
            if self._git_fetch(source):
                break
        else:
            logger.warning("Git fetch failed")
            raise ExecutionError(
                "Git fetch failed: cannot reach the remote repository. "
                "Check the network connection or the repository address."
            )

        local_sha, _, _, _ = self.get_commit()
        sha1, _, _, message = self.get_commit(f"{source}/{self.Branch}")

        if sha1 and local_sha != sha1:
            logger.info(f"Update available")
            logger.info(f"{sha1[:8]} - {message}")
            return True
        else:
            logger.info(f"No update")
            return False

    def _check_update_(self) -> bool:
        """
        Deprecated
        """
        self.state = "checking"
        r = self.Repository.split("/")
        owner = r[3]
        repo = r[4]
        if "gitee" in r[2]:
            base = "https://gitee.com/api/v5/repos/"
            headers = {}
            token = self.config["ApiToken"]
            if token:
                para = {"access_token": token}
        else:
            base = "https://api.github.com/repos/"
            headers = {"Accept": "application/vnd.github.v3.sha"}
            para = {}
            token = self.config["ApiToken"]
            if token:
                headers["Authorization"] = "token " + token

        try:
            list_commit = requests.get(
                base + f"{owner}/{repo}/branches/{self.Branch}",
                headers=headers,
                params=para,
            )
        except Exception as e:
            logger.exception(e)
            logger.warning("Check update failed")
            return 0

        if list_commit.status_code != 200:
            logger.warning(f"Check update failed, code {list_commit.status_code}")
            return 0
        try:
            sha = list_commit.json()["commit"]["sha"]
        except Exception as e:
            logger.exception(e)
            logger.warning("Check update failed when parsing return json")
            return 0

        local_sha, _, _, _ = self._get_local_commit()

        if sha == local_sha:
            logger.info("No update")
            return 0

        try:
            get_commit = requests.get(
                base + f"{owner}/{repo}/commits/" + local_sha,
                headers=headers,
                params=para,
            )
        except Exception as e:
            logger.exception(e)
            logger.warning("Check update failed")
            return 0

        if get_commit.status_code != 200:
            # for develops
            logger.info(
                f"Cannot find local commit {local_sha[:8]} in upstream, skip update"
            )
            return 0

        logger.info(f"Update {sha[:8]} available")
        return 1

    def check_update(self):
        if not self._operation_lock.acquire(blocking=False):
            return
        try:
            self._check_update_locked()
        finally:
            self._operation_lock.release()

    def _check_update_locked(self):
        if self.state == "checking":
            # Escape hatch for a wedged check (e.g. the thread was killed
            # mid-fetch): allow a retry after 10 minutes instead of blocking
            # all checks until the backend restarts.
            if time.time() - self._check_started_at < 600:
                return
        elif self.state not in (0, "failed", "finish"):
            return
        self._check_started_at = time.time()
        result = self._check_update()
        # _check_update() sets state to "failed" itself when the check failed;
        # don't let the boolean return value overwrite that, or the failure
        # would be reported as a clean "no update" (state 0) on the UI.
        if self.state != "failed":
            self.state = result

    def git_update(self):
        self.read()
        if self.UpdateSource == 'mirrorchyan':
            return super().git_update()
        return self._git_update_with_retry()

    @retry(ExecutionError, tries=3, delay=5, logger=None)
    def _git_update_with_retry(self):
        return super().git_update()

    @retry(ExecutionError, tries=3, delay=5, logger=None)
    def pip_install(self):
        return super().pip_install()

    def update(self):
        logger.hr("Run update")
        self.update_error = ''
        self.check_error = ''
        self.recovery_failed = False
        try:
            self.git_update()
            self.invalidate_dependency_cache()
            self.pip_install()
            self.mirror_updater().ready()
            if State.restart_event is not None:
                self.mirror_updater().prepare_launch()
        except (ExecutionError, OSError, ValueError) as error:
            self.update_error = str(error)
            logger.warning(self.update_error)
            try:
                self.mirror_updater().rollback()
                self.invalidate_dependency_cache()
            except (ExecutionError, OSError, ValueError) as recovery_error:
                self.recovery_failed = True
                self.update_error += f' 恢复失败：{recovery_error}'
                logger.error(self.update_error)
                self.mirror_updater().release()
            return False
        return True

    def run_update(self):
        if self.state not in ("failed", 0, 1):
            return
        if not self._operation_lock.acquire(blocking=False):
            return
        try:
            self._start_update()
        finally:
            self._operation_lock.release()

    def _start_update(self):
        try:
            self.mirror_updater().acquire()
        except ExecutionError as error:
            self.state = 'failed'
            self.update_error = str(error)
            return
        self.state = "start"
        instances = ProcessManager.running_instances()
        names = []
        for nkas in instances:
            names.append(nkas.config_name + "\n")

        logger.info("Waiting all running nkas finish.")
        self._wait_update(instances, names)

    def _wait_update(self, instances: List[ProcessManager], names):
        if self.state == "cancel":
            self.state = 1
        self.state = "wait"
        self.event.set()
        _instances = instances.copy()
        start_time = time.time()
        while _instances:
            for nkas in _instances:
                if not nkas.alive:
                    _instances.remove(nkas)
                    logger.info(f"NKAS [{nkas.config_name}] stopped")
                    logger.info(f"Remains: {[nkas.config_name for nkas in _instances]}")
            if self.state == "cancel":
                self.state = 1
                self.event.clear()
                self.mirror_updater().release()
                ProcessManager.restart_processes(instances, self.event)
                return
            time.sleep(0.25)
            if time.time() - start_time > 60 * 10:
                logger.warning("Waiting nkas shutdown timeout, force kill")
                for nkas in _instances:
                    nkas.stop()
                break
        self._run_update(instances, names)

    def _run_update(self, instances, names):
        self.state = "run update"
        logger.info("All nkas stopped, start updating")

        if self.update():
            if State.restart_event is not None:
                self.state = "reload"
                with open("./config/reloadnkas", mode="w") as f:
                    f.writelines(names)
                from module.webui.app import clearup

                clearup()
                self._trigger_reload(0)
            else:
                self.state = "finish"
        else:
            self.state = "failed"
            logger.warning("Update failed")
            if not self.recovery_failed:
                self.event.clear()
                ProcessManager.restart_processes(instances, self.event)
            else:
                logger.error('Instance restart blocked because source recovery failed')
            return False

    @staticmethod
    def _trigger_reload(delay=2):
        def trigger():
            # with open("./config/reloadflag", mode="w"):
            #     # app ended here and uvicorn will restart whole app
            #     pass
            State.restart_event.set()

        timer = threading.Timer(delay, trigger)
        timer.start()

    def schedule_update(self) -> Generator:
        th: TaskHandler
        th = yield
        if self.schedule_time is None:
            th.remove_current_task()
            yield
        th._task.delay = get_next_time(self.schedule_time)
        yield
        while True:
            self.check_update()
            if self.state != 1 or self.UpdateSource == 'mirrorchyan' and self.source_info.get('needs_cdk'):
                th._task.delay = get_next_time(self.schedule_time)
                yield
                continue
            if State.restart_event is None:
                yield
                continue
            if not self.run_update():
                self.state = "failed"
            th._task.delay = get_next_time(self.schedule_time)
            yield

    def cancel(self):
        self.state = "cancel"


updater = Updater()

if __name__ == "__main__":
    pass
    # if updater.check_update():
    updater.update()
