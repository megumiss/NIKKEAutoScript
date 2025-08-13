import datetime
import subprocess
import threading
import time
from typing import Generator, Tuple, List

from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager
from deploy.utils import DEPLOY_CONFIG
from module.base.retry import retry
from module.logger import logger
from module.webui.process_manager import ProcessManager
from module.webui.setting import State
from module.webui.utils import TaskHandler
from module.webui.utils import TaskHandler, get_next_time


class Updater(GitManager, PipManager):
    def __init__(self, file=DEPLOY_CONFIG):
        super().__init__(file=file)
        # 更新器状态
        self.state = 0
        # 进程同步标识
        self.event: threading.Event = None

    @property
    def delay(self):
        self.read()
        return int(self.CheckUpdateInterval) * 60

    @property
    def schedule_time(self):
        self.read()
        t = self.AutoRestartTime
        if t is not None:
            return datetime.time.fromisoformat(t)
        else:
            return None

    @retry(ExecutionError, tries=3, delay=5, logger=None)
    def git_update(self):
        return super().git_update()

    @retry(ExecutionError, tries=3, delay=5, logger=None)
    def pip_install(self):
        return super().pip_install()

    def execute_output(self, command) -> str:
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        log = subprocess.run(
            command, capture_output=True, text=True, encoding="utf8", shell=True
        ).stdout
        return log

    def get_commit(self, revision="", n=1, short_sha1=False) -> Tuple:
        """
        Return:
            (sha1, author, isotime, message,)
        """
        ph = "h" if short_sha1 else "H"

        log = self.execute_output(
            f'"{self.git}" log {revision} --pretty=format:"%{ph}---%an---%ad---%s" --date=iso -{n}'
        )

        if not log:
            return None, None, None, None

        logs = log.split("\n")
        logs = list(map(lambda log: tuple(log.split("---")), logs))

        if n == 1:
            return logs[0]
        else:
            return logs

    def update(self):
        logger.hr("Run update")
        try:
            self.git_update()
            self.pip_install()
        except ExecutionError:
            return False
        return True

    def _check_update(self) -> bool:
        self.state = "checking"
        source = "origin"
        for _ in range(3):
            """
                从上游仓库拉取最新的数据，但不进行合并
            """
            if self.execute(f'"{self.git}" fetch {source} {self.Branch}', allow_failure=True):
                break
        else:
            logger.warning("Git fetch failed")
            return False

        log = self.execute_output(
            f'"{self.git}" log --not --remotes={source}/* -1 --oneline'
        )
        if log:
            logger.info(
                f"Cannot find local commit {log.split()[0]} in upstream, skip update"
            )
            return False

        """
             当本地和上游仓库不一样时，会返回上游仓库最新的commit，如果一样，则返回None 
             git.exe log ..origin/master --pretty=format:"h---%an---%ad---%s" --date=iso -1
             
             返回上游仓库最新的commit
             git.exe log origin/master --pretty=format:"h---%an---%ad---%s" --date=iso -1
        """
        sha1, _, _, message = self.get_commit(f"..{source}/{self.Branch}")

        if sha1:
            logger.info(f"New update available")
            logger.info(f"{sha1[:8]} - {message}")
            return True
        else:
            logger.info(f"No update")
            return False

    def check_update(self):
        if self.state in (0, "failed", "finish"):
            self.state = self._check_update()

    def run_update(self):
        if self.state not in ("failed", 0, 1):
            return
        self._start_update()

    def _start_update(self):
        self.state = "start"
        instances = ProcessManager.running_instances()
        names = []
        for nkas in instances:
            names.append(nkas.config_name + "\n")

        logger.info("Waiting nkas in running finish.")
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
        logger.info("nkas stopped, start updating")
        if self.update():
            if State.restart_event is not None:
                self.state = "reload"
                with open("./config/reloadnkas", mode="w") as f:
                    f.writelines(names)
                from module.webui.app import clearup

                self._trigger_reload(2)
                clearup()
            else:
                self.state = "finish"
        else:
            self.state = "failed"
            logger.warning("Update failed")
            self.event.clear()
            ProcessManager.restart_processes(instances, self.event)
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
            if self.state != 1:
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
    updater.update()
