import copy
import operator
from datetime import datetime, timedelta
import threading
import time

from module.base.filter import Filter
from module.base.utils import ensure_time
from module.config.config_generated import GeneratedConfig
from module.config.config_updater import ConfigUpdater
from module.config.manual_config import ManualConfig
from module.config.utils import deep_get, DEFAULT_TIME, deep_set, filepath_config, path_to_arg, dict_to_kv, \
    get_server_next_update, nearest_future
from module.config.watcher import ConfigWatcher
from module.exception import ScriptError, RequestHumanTakeover
from module.logger import logger


class TaskEnd(Exception):
    pass


class Function:
    def __init__(self, data):
        self.enable = deep_get(data, keys="Scheduler.Enable", default=False)
        self.command = deep_get(data, keys="Scheduler.Command", default="Unknown")
        self.next_run = deep_get(data, keys="Scheduler.NextRun", default=DEFAULT_TIME)

    def __str__(self):
        enable = "Enable" if self.enable else "Disable"
        return f"{self.command} ({enable}, {str(self.next_run)})"

    __repr__ = __str__

    def __eq__(self, other):
        if not isinstance(other, Function):
            return False

        if self.command == other.command and self.next_run == other.next_run:
            return True
        else:
            return False


def name_to_function(name):
    """
    Args:
        name (str):

    Returns:
        Function:
    """
    function = Function({})
    function.command = name
    function.enable = True
    return function


class NikkeConfig(ConfigUpdater, ManualConfig, GeneratedConfig, ConfigWatcher):
    stop_event: threading.Event = None
    bound = {}
    
    def __setattr__(self, key, value):
        if key in self.bound:
            path = self.bound[key]
            self.modified[path] = value
            if self.auto_update:
                self.update()
        else:
            super().__setattr__(key, value)
    
    def __init__(self, config_name, task=None):
        self.config_name = config_name
        self.data = {}
        self.modified = {}
        self.bound = {}
        self.auto_update = True
        self.overridden = {}
        self.pending_task = []
        self.waiting_task = []
        self.task: Function
        self.is_template_config = config_name == "template"

        if self.is_template_config:
            logger.info("Using template config, which is read only")
            self.auto_update = False
            self.task = name_to_function("template")
        else:
            self.load()

            if task is None:
                # Bind `Alas` by default which includes emulator settings.
                task = name_to_function("NKAS")
            else:
                # Bind a specific task for debug purpose.
                task = name_to_function(task)

            logger.info(f'task: {task.__str__()}')

            self.bind(task)
            self.task = task
            self.save()

        # logger.attr("Server", self.SERVER)
        logger.attr("Server", 'intl' if 'proximabeta' in self.Emulator_PackageName else 'tw')

    def load(self):
        self.data = self.read_file(self.config_name)
        self.config_override()
        for path, value in self.modified.items():
            deep_set(self.data, keys=path, value=value)

    def bind(self, func, func_set=None):
        """
        Args:
            func (str, Function): Function to run
            func_set (set): Set of tasks to be bound
        """
        if func_set is None:
            func_set = {"General", "NKAS"}

        if isinstance(func, Function):
            func = func.command

        func_set.add(func)

        logger.info(f"Bind task {func_set}")

        # Bind arguments
        visited = set()
        self.bound.clear()
        for func in func_set:
            func_data = self.data.get(func, {})
            for group, group_data in func_data.items():
                for arg, value in group_data.items():
                    path = f"{group}.{arg}"
                    if path in visited:
                        continue
                    """
                        将 func_set 任务组 / 选项组 的 属性覆盖到 GeneratedConfig 的类变量
                    """
                    arg = path_to_arg(path)
                    super().__setattr__(arg, value)
                    self.bound[arg] = f"{func}.{path}"
                    visited.add(path)

        # Override arguments
        for arg, value in self.overridden.items():
            super().__setattr__(arg, value)

    def save(self, mod_name='nkas'):
        if not self.modified:
            return False

        for path, value in self.modified.items():
            deep_set(self.data, keys=path, value=value)

        logger.info(
            f"Save config {filepath_config(self.config_name, mod_name)}, {dict_to_kv(self.modified)}"
        )
        # Don't use self.modified = {}, that will create a new object.
        self.modified.clear()
        self.write_file(self.config_name, data=self.data)

    def update(self):
        self.load()
        self.config_override()
        self.bind(self.task)
        self.save()

    def config_override(self):
        now = datetime.now().replace(microsecond=0)
        limited = set()

        def limit_next_run(tasks, limit):
            for task in tasks:
                if task in limited:
                    continue
                limited.add(task)
                next_run = deep_get(
                    self.data, keys=f"{task}.Scheduler.NextRun", default=None
                )
                if isinstance(next_run, datetime) and next_run > limit:
                    deep_set(self.data, keys=f"{task}.Scheduler.NextRun", value=now)

        for task in ["Reward"]:
            if not self.is_task_enabled(task):
                self.modified[f"{task}.Scheduler.Enable"] = True
        # force_enable = list
        #
        # force_enable(
        #     [
        #         "Reward",
        #     ]
        # )
        # 当运行时间大于24小时之后时，设为现在运行
        limit_next_run(["Reward"], limit=now + timedelta(hours=24, seconds=-1))
        # limit_next_run(self.args.keys(), limit=now + timedelta(hours=24, seconds=-1))
        # 选择最新的活动
        for task in ["Event"]:
            # deep_set(self.data, keys=f"{task}.Event.Event", value=self.EVENTS[0].get('event_id'))
            self.modified[f"{task}.Event.Event"] = self.EVENTS[0].get('event_id')

    def get_next(self):
        """
        Returns:
            Function: Command to run
        """
        self.get_next_task()

        if self.pending_task:
            NikkeConfig.is_hoarding_task = False
            logger.info(f"Pending tasks: {[f.command for f in self.pending_task]}")
            task = self.pending_task[0]
            logger.attr("Task", task)
            return task
        else:
            NikkeConfig.is_hoarding_task = True

        if self.waiting_task:
            logger.info("No task pending")
            task = copy.deepcopy(self.waiting_task[0])
            """
                Alas：囤积任务，延迟X分钟执行
                task.next_run = (task.next_run + self.hoarding).replace(microsecond=0)
            """
            logger.attr("Task", task)
            return task
        else:
            logger.critical("No task waiting or pending")
            logger.critical("Please enable at least one task")
            raise RequestHumanTakeover

    def get_next_task(self):
        pending = []
        waiting = []
        error = []
        now = datetime.now()

        # func 为json中的任务属性
        """
        {
            'Scheduler': 
                {
                    'Enable': True, 
                    'NextRun': datetime.datetime(1989, 12, 27, 0, 0),
                    'Command': 'Restart',         
                    'SuccessInterval': 0,
                    'FailureInterval': 0, 
                    'ServerUpdate': '04:00'
                 },  
            'Storage': 
                {
                    'Storage': {}
                }
        } 
        
        Reward (Enable, 1989-12-27 00:00:00)
        """
        for func in self.data.values():
            func = Function(func)
            """
                跳过Scheduler.Enable为False的任务
            """
            if not func.enable:
                continue
            """
                从配置中获取的运行时间格式错误
            """
            if not isinstance(func.next_run, datetime):
                error.append(func)

            elif func.next_run < now:
                """
                    当前时间 > 该任务的下次运行时间
                """
                pending.append(func)
            else:
                waiting.append(func)

        """
            任务优先级
        """
        f = Filter(regex=r"(.*)", attr=["command"])
        f.load(self.SCHEDULER_PRIORITY)
        if pending:
            """
                待执行队列不进行排序，因为会影响到重启任务
            """
            pending = f.apply(pending)
        if waiting:
            """
                等待队列按运行时间排序显示
            """
            waiting = f.apply(waiting)
            waiting = sorted(waiting, key=operator.attrgetter("next_run"))
        if error:
            pending = error + pending

        self.pending_task = pending
        self.waiting_task = waiting

    def is_task_enabled(self, task):
        return bool(self.cross_get(keys=[task, 'Scheduler', 'Enable'], default=False))

    def cross_get(self, keys, default=None):
        """
        Get configs from other tasks.

        Args:
            keys (str, list[str]): Such as `{task}.Scheduler.Enable`
            default:

        Returns:
            Any:
        """
        return deep_get(self.data, keys=keys, default=default)

    def task_delay(self, success=None, server_update=None, target=None, minute=None, task=None):
        def ensure_delta(delay):
            return timedelta(seconds=int(ensure_time(delay, precision=3) * 60))

        run = []
        if success is not None:
            interval = (
                self.Scheduler_SuccessInterval
                if success
                else self.Scheduler_FailureInterval
            )
            run.append(datetime.now() + ensure_delta(interval))
        """
            服务器更新时
        """
        if server_update is not None:
            if server_update is True:
                server_update = self.Scheduler_ServerUpdate
            run.append(get_server_next_update(server_update))
        if target is not None:
            target = [target] if not isinstance(target, list) else target
            target = nearest_future(target)
            run.append(target)
        if minute is not None:
            run.append(datetime.now() + ensure_delta(minute))

        if len(run):
            run = min(run).replace(microsecond=0)
            kv = dict_to_kv(
                {
                    "success": success,
                    "server_update": server_update,
                    "target": target,
                    "minute": minute,
                },
                allow_none=False,
            )
            if task is None:
                task = self.task.command
            logger.info(f"Delay task `{task}` to {run} ({kv})")
            self.modified[f'{task}.Scheduler.NextRun'] = run
            self.update()
        else:
            raise ScriptError(
                "Missing argument in delay_next_run, should set at least one"
            )

    def task_call(self, task, force_call=True):
        if not deep_get(self.data, keys=f"{task}.Scheduler.NextRun", default=None):
            raise ScriptError(f"Task to call: `{task}` does not exist in user config")

        if force_call or self.is_task_enabled(task):
            logger.info(f"Task call: {task}")
            self.modified[f"{task}.Scheduler.NextRun"] = datetime.now().replace(
                microsecond=0
            )
            self.modified[f"{task}.Scheduler.Enable"] = True
            if self.auto_update:
                self.update()
            return True
        else:
            logger.info(f"Task call: {task} (skipped because disabled by user)")
            return False

    def override(self, **kwargs):
        """
        Override anything you want.
        Variables stall remain overridden even config is reloaded from yaml file.
        Note that this method is irreversible.
        """
        for arg, value in kwargs.items():
            self.overridden[arg] = value
            super().__setattr__(arg, value)

    @property
    def EVENT_TYPE(self):
        return self.EVENTS[0]["event_type"]

    @property
    def EVENT_COOP(self):
        return self.Coop_EventCoop
