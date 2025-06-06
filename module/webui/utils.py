import datetime
import operator
import re
import threading
import time
from typing import Callable, Generator, List

from pywebio.session import register_thread, run_js, eval_js
from rich.terminal_theme import TerminalTheme

from module.config.utils import deep_iter
from module.logger import logger


class Task:
    def __init__(
            self, g: Generator, delay: float, next_run: float = None, name: str = None
    ) -> None:
        self.g = g
        g.send(None)
        self.delay = delay
        self.next_run = next_run if next_run else time.time()
        self.name = name if name is not None else self.g.__name__

    def __str__(self) -> str:
        return f"<{self.name} (delay={self.delay})>"

    def __next__(self) -> None:
        return next(self.g)

    def send(self, obj) -> None:
        return self.g.send(obj)

    __repr__ = __str__


class TaskHandler:
    def __init__(self) -> None:
        # List of background running task
        self.tasks: List[Task] = []
        # List of task name to be removed
        self.pending_remove_tasks: List[Task] = []
        # Running task
        self._task = None
        # Task running thread
        self._thread: threading.Thread = None
        self._alive = False
        self._lock = threading.Lock()

    def _get_thread(self) -> threading.Thread:
        logger.info('get thread in TaskHandler')
        thread = threading.Thread(target=self.loop, daemon=True)
        return thread

    def start(self) -> None:
        """
        Start task handler.
        """
        logger.info("Start task handler")
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Task handler already running!")
            return
        self._thread = self._get_thread()
        self._thread.start()

    def add(self, func, delay: float, pending_delete: bool = False) -> None:
        """
        Add a task running background.
        Another way of `self.add_task()`.
        func: Callable or Generator
        """
        if isinstance(func, Callable):
            g = get_generator(func)
        elif isinstance(func, Generator):
            g = func
        self.add_task(Task(g, delay), pending_delete=pending_delete)

    def add_task(self, task: Task, pending_delete: bool = False) -> None:
        """
        Add a task running background.
        """

        if task in self.tasks:
            logger.warning(f"Task {task} already in tasks list.")
            return

        # if task.name in list(map(lambda x: x.name, self.tasks)):
        #     logger.warning(f"Task {task} already in tasks list.")
        #     return

        logger.info(f"Add task {task}")
        with self._lock:
            self.tasks.append(task)
        if pending_delete:
            self.pending_remove_tasks.append(task)

    def _remove_task(self, task: Task) -> None:
        if task in self.tasks:
            self.tasks.remove(task)

    def remove_task(self, task: Task, nowait: bool = False) -> None:
        """
        Remove a task in `self.tasks`.
        Args:
            task:
            nowait: if True, remove it right now,
                    otherwise remove when call `self.remove_pending_task`
        """
        if nowait:
            with self._lock:
                self._remove_task(task)
        else:
            self.pending_remove_tasks.append(task)

    def remove_pending_task(self) -> None:
        """
        Remove all pending remove tasks.
        """
        with self._lock:
            for task in self.pending_remove_tasks:
                self._remove_task(task)
            self.pending_remove_tasks = []

    def remove_current_task(self) -> None:
        self.remove_task(self._task, nowait=True)

    def loop(self) -> None:
        """
        Start task loop.
        You **should** run this function in an individual thread.
        """
        self._alive = True
        while self._alive:
            if self.tasks:
                with self._lock:
                    self.tasks.sort(key=operator.attrgetter("next_run"))
                    task = self.tasks[0]
                if task.next_run < time.time():
                    start_time = time.time()
                    try:
                        self._task = task
                        # logger.debug(f'Start task {task.g.__name__}')
                        task.send(self)
                        # logger.debug(f'End task {task.g.__name__}')
                    except Exception as e:
                        logger.exception(e)
                        self.remove_task(task, nowait=True)
                    finally:
                        self._task = None
                    end_time = time.time()
                    task.next_run += task.delay
                    with self._lock:
                        for task in self.tasks:
                            task.next_run += end_time - start_time
                else:
                    time.sleep(0.05)
            else:
                time.sleep(0.5)

    def stop(self) -> None:
        self.remove_pending_task()
        self._alive = False
        self._thread.join(timeout=2)
        if not self._thread.is_alive():
            logger.info("Finish task handler")
        else:
            logger.warning("Task handler does not stop within 2 seconds")


class WebIOTaskHandler(TaskHandler):
    def _get_thread(self) -> threading.Thread:
        thread = super()._get_thread()
        logger.info('register thread in WebIOTaskHandler')
        register_thread(thread)
        return thread


def add_css(filepath):
    with open(filepath, "r") as f:
        css = f.read().replace("\n", "")
        run_js(f"""$('head').append('<style>{css}</style>')""")


def set_localstorage(key, value):
    return run_js("localStorage.setItem(key, value)", key=key, value=value)


def get_localstorage(key):
    return eval_js("localStorage.getItem(key)", key=key)


def get_generator(func: Callable):
    def _g():
        yield
        while True:
            yield func()

    g = _g()
    g.__name__ = func.__name__
    return g


LOG_CODE_FORMAT = """\
    <span style="font-family:Menlo,'DejaVu Sans Mono',consolas,'Courier New',monospace">{code}</span>
"""

# LOG_CODE_FORMAT = """\
#    {code}
# """

DARK_TERMINAL_THEME = TerminalTheme(
    (30, 30, 30),  # Background
    (204, 204, 204),  # Foreground
    [
        (0, 0, 0),  # Black
        (205, 49, 49),  # Red
        (13, 188, 121),  # Green
        (229, 229, 16),  # Yellow
        (36, 114, 200),  # Blue
        (188, 63, 188),  # Purple / Magenta
        (17, 168, 205),  # Cyan
        (229, 229, 229),  # White
    ],
    [  # Bright
        (102, 102, 102),  # Black
        (241, 76, 76),  # Red
        (35, 209, 139),  # Green
        (245, 245, 67),  # Yellow
        (59, 142, 234),  # Blue
        (214, 112, 214),  # Purple / Magenta
        (41, 184, 219),  # Cyan
        (229, 229, 229),  # White
    ],
)


class Switch:
    def __init__(self, status, get_state, name=None):
        """
        Args:
            status
                (dict):A dict describes each state.
                    {
                        0: {
                            'func': (Callable)
                        },
                        1: {
                            'func'
                            'args': (Optional, tuple)
                            'kwargs': (Optional, dict)
                        },
                        2: [
                            func1,
                            {
                                'func': func2
                                'args': args2
                            }
                        ]
                        -1: []
                    }
                (Callable):current state will pass into this function
                    lambda state: do_update(state=state)
            get_state:
                (Callable):
                    return current state
                (Generator):
                    yield current state, do nothing when state not in status
            name:
        """
        self._lock = threading.Lock()
        self.name = name
        self.status = status
        self.get_state = get_state
        if isinstance(get_state, Generator):
            self._generator = get_state
        elif isinstance(get_state, Callable):
            self._generator = self._get_state()

    @staticmethod
    def get_state():
        pass

    def _get_state(self):
        """
        Predefined generator when `get_state` is an callable
        Customize it if you have multiple criteria on state
        """

        _status = self.get_state()
        yield _status
        while True:
            status = self.get_state()
            if _status != status:
                _status = status
                yield _status
                continue
            yield -1

    def switch(self):
        with self._lock:
            """
                获得按钮状态
            """
            r = next(self._generator)

        if callable(self.status):
            self.status(r)
        elif r in self.status:
            f = self.status[r]
            if isinstance(f, (dict, Callable)):
                f = [f]
            for d in f:
                if isinstance(d, Callable):
                    d = {"func": d}
                func = d["func"]
                args = d.get("args", tuple())
                kwargs = d.get("kwargs", dict())
                func(*args, **kwargs)

    def g(self) -> Generator:
        g = get_generator(self.switch)
        if self.name:
            name = self.name
        else:
            name = self.get_state.__name__
        g.__name__ = f"Switch_{name}_refresh"
        return g


def get_nkas_config_listen_path(args):
    for path, d in deep_iter(args, depth=3):
        if d.get("display") in ["readonly", "hide"]:
            continue
        yield path


str2type = {
    "str": str,
    "float": float,
    "int": int,
    "bool": bool,
    "ignore": lambda x: x,
}


def parse_pin_value(val, valuetype: str = None):
    """
    input, textarea return str
    select return its option (str or int)
    checkbox return [] or [True] (define in put_checkbox_)
    """
    if isinstance(val, list):
        if len(val) == 0:
            return False
        else:
            return True
    elif valuetype:
        return str2type[valuetype](val)
    elif isinstance(val, (int, float)):
        return val
    else:
        try:
            v = float(val)
        except ValueError:
            return val
        if v.is_integer():
            return int(v)
        else:
            return v


RE_DATETIME = (
    r"\d{4}\-(0\d|1[0-2])\-([0-2]\d|[3][0-1]) "
    r"([0-1]\d|[2][0-3]):([0-5]\d):([0-5]\d)"
)


def re_fullmatch(pattern, string):
    if pattern == "datetime":
        pattern = RE_DATETIME
    # elif:
    return re.fullmatch(pattern=pattern, string=string)

def get_next_time(t: datetime.time):
    now = datetime.datetime.today().time()
    second = (
        (t.hour - now.hour) * 3600
        + (t.minute - now.minute) * 60
        + (t.second - now.second)
    )
    if second < 0:
        second += 86400
    return second
