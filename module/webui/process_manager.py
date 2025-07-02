import os
import queue
import threading
from multiprocessing import Process
from typing import List, Dict, Union

from filelock import FileLock
from rich.console import ConsoleRenderable

from main import NikkeAutoScript
from module.config.utils import filepath_config
from module.logger import set_file_logger, set_func_logger, logger
from module.submodule.utils import mod_instance, get_config_mod
from module.webui.setting import State


class ProcessManager:
    # 当前进程列表
    _processes: Dict[str, "ProcessManager"] = {}

    def __init__(self, config_name: str = "nkas") -> None:
        # 配置名称
        self.config_name = config_name
        # 进程共享渲染队列
        self._renderable_queue: queue.Queue[ConsoleRenderable] = State.manager.Queue()
        # 渲染后队列
        self.renderables: List[ConsoleRenderable] = []
        # 最大渲染数
        self.renderables_max_length = 400
        self.renderables_reduce_length = 80
        # 进程实例
        self._process: Process = None
        # 日志队列处理线程
        self.thd_log_queue_handler: threading.Thread = None

    @classmethod
    def get_manager(cls, config_name: str) -> "ProcessManager":
        if config_name not in cls._processes:
            logger.info(f'create ProcessManager: {config_name}')
            cls._processes[config_name] = ProcessManager(config_name)
        return cls._processes[config_name]

    @staticmethod
    def run_process(
            config_name, func: str, q: queue.Queue, e: threading.Event = None
    ) -> None:
        set_func_logger(func=q.put)
        """
            从logger.py 93行
            重写Rich的RichHandler类
            将渲染后的log通过Queue().put(item)存入State.manager.Queue()中，进程共享渲染队列
        """

        set_file_logger(name=config_name)
        
        from module.config.config import NikkeConfig
        NikkeConfig.stop_event = e
        
        try:
            if e is not None:
                NikkeAutoScript.stop_event = e
            NikkeAutoScript(config_name=config_name).loop()
            logger.info(f"[{config_name}] exited. Reason: Finish\n")
        except Exception as e:
            logger.exception(e)

    def start(self, func, ev: threading.Event = None) -> None:
        if not self.alive:
            """
             run_process(config_name, func: str, q: queue.Queue, e: threading.Event = None)
                q: State.manager.Queue() 进程共享渲染队列
                e: 进程同步标识
                func(mod_name): 创建进程执行的方法，在Alas中，默认为执行
                AzurLaneAutoScript(config_name='nkas').loop()
            """
            self._process = Process(
                target=ProcessManager.run_process,
                args=(
                    self.config_name,
                    func,
                    self._renderable_queue,
                    ev,
                ),
            )
            self._process.start()
            self.start_log_queue_handler()

    def start_log_queue_handler(self):
        if self.thd_log_queue_handler and self.thd_log_queue_handler.is_alive():
            return
        """
           创建跟当前进程关联的日志处理线程，并运行
        """
        self.thd_log_queue_handler = threading.Thread(
            target=self._thread_log_queue_handler
        )
        self.thd_log_queue_handler.start()

    def _thread_log_queue_handler(self) -> None:
        while self.alive:
            try:
                """
                    从logger.py 93行
                    重写Rich的RichHandler类
                    将渲染后的log通过Queue().put(item)存入State.manager.Queue()中，进程共享渲染队列
                """
                log = self._renderable_queue.get(timeout=1)
            except queue.Empty:
                continue
            """
                从进程共享渲染队列获取渲染后的日志
                日志队列大于400时，截取第80个到最后一位
                然后日志会通过在base.py中创建的WebIOTaskHandler，继承TaskHandler类的子任务处理器
                在app.py 101行中，添加到子任务处理器的log.put_log(ProcessManager)方法
                put_log参数为ProcessManager.get_manager()创建的ProcessManager实例
                
                log = RichLog("log")
                self.task_handler.add(log.put_log(self.nkas), delay=0.25, pending_delete=True)
                
                TaskHandler会在后台不断执行put_log方法
                然后日志会通过在webui/widgets.py中的put_log方法渲染到web界面
            """
            self.renderables.append(log)
            if len(self.renderables) > self.renderables_max_length:
                self.renderables = self.renderables[self.renderables_reduce_length:]
        logger.info("End of log queue handler loop")

    @classmethod
    def running_instances(cls) -> List["ProcessManager"]:
        l = []
        for process in cls._processes.values():
            if process.alive:
                l.append(process)
        return l

    @staticmethod
    def restart_processes(
            instances: List[Union["ProcessManager", str]] = None, ev: threading.Event = None
    ):
        """
        After update and reload, or failed to perform an update,
        restart all nkas that running before update
        """

        """
            在Alas中，启动uvicorn时，会调用restart_processes
        """

        logger.hr("Restart nkas")

        # Load MOD_CONFIG_DICT

        """
            加载非模板的实例名称(配置名称)
        """
        mod_instance()

        """
            instances在更新失败或取消时，为开始更新前运行的Alas实例，类型为ProcessManager
            
            当instances存储的类型为str时，在Alas启动uvicorn时
            为从deploy.yaml读取Deploy.Webui.Run的实例名称(配置名称)，类型为list
        """
        if instances is None:
            instances = []

        _instances = set()

        for instance in instances:
            if isinstance(instance, str):
                _instances.add(ProcessManager.get_manager(instance))
            elif isinstance(instance, ProcessManager):
                _instances.add(instance)

        """
            instances在热更新成功后，重新启动uvicorn时
            在Alas中，为开始更新前保存到 ./config/reloadnkas 中的实例名称(配置名称)
        """
        try:
            with open("./config/reloadnkas", mode="r") as f:
                for line in f.readlines():
                    line = line.strip()
                    _instances.add(ProcessManager.get_manager(line))
        except FileNotFoundError:
            pass

        """
            当实例非nkas实例时
            通过实例名称(配置名称)，读取存储在./config的配置，也就是mod名称(实例种类)
            mod_name应该为nkas，Daemon，AzurLaneUncensored，Benchmark，GameManager，maa，MaaCopilot
            例如: maa1.maa.json 
            config_name为maa1，mod_name为maa
            
            当有多个nkas实例时，读取为实例名称(配置名称).json
            这样get_config_mod会因为KeyError，返回 'nkas'， 然后该实例在运行时，配置为实例名称(配置名称).json
        """
        for process in _instances:
            logger.info(f"Starting [{process.config_name}]")
            process.start(func=get_config_mod(process.config_name), ev=ev)

        try:
            os.remove("./config/reloadnkas")
        except:
            pass
        logger.info("Start nkas complete")

    @property
    def alive(self) -> bool:
        if self._process is not None:
            return self._process.is_alive()
        else:
            return False

    def stop(self) -> None:
        lock = FileLock(f"{filepath_config(self.config_name)}.lock")
        with lock:
            if self.alive:
                self._process.kill()
                self.renderables.append(
                    f"[{self.config_name}] exited. Reason: Manual stop\n"
                )
            if self.thd_log_queue_handler is not None:
                self.thd_log_queue_handler.join(timeout=1)
                if self.thd_log_queue_handler.is_alive():
                    logger.warning(
                        "Log queue handler thread does not stop within 1 seconds"
                    )
        logger.info(f"[{self.config_name}] exited")
