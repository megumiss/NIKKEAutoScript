"""Task actions with the same scheduling semantics as the automation loop."""

from module.config.utils import filepath_args, read_file
from module.submodule.utils import get_available_func, get_available_mod, get_available_mod_func, get_config_mod, load_config
from module.webui.process_manager import ProcessManager
from module.webui.updater import updater


class TaskService:
    def queue_now(self, config_name: str, task: str) -> bool:
        """
        Mark a normal configured task for immediate scheduler execution.

        Reads, modifies and writes the whole config file. Call it on the event
        loop like the other Web config writers, so concurrent requests cannot
        overwrite each other's changes.
        """
        tasks = read_file(filepath_args('args', 'nkas'))
        if task not in tasks:
            return False

        config = load_config(config_name)
        config.load()
        if not config.task_call(task, force_call=True):
            return False
        config.save()
        return True

    def ensure_scheduler(self, config_name: str) -> bool:
        """
        Start the instance scheduler if it is not running. May wait for an
        in-progress stop cleanup, so call it off the event loop.

        Returns:
            bool: True if this call started the scheduler.
        """
        return ProcessManager.get_manager(config_name).start(get_config_mod(config_name), ev=updater.event)

    def run_now(self, config_name: str, task: str):
        """Queue a normal configured task for immediate scheduler execution."""
        if not self.queue_now(config_name, task):
            return None
        return {'task': task, 'started_scheduler': self.ensure_scheduler(config_name)}

    def start_tool(self, config_name: str, task: str):
        available = set(get_available_func()) | set(get_available_mod()) | set(get_available_mod_func())
        if task not in available:
            return None
        # start() checks and starts under the lifecycle lock; a racing request
        # that loses gets False instead of a misleading "started".
        if not ProcessManager.get_manager(config_name).start(task, ev=updater.event):
            return {'task': task, 'already_running': True}
        return {'task': task, 'started': True}
