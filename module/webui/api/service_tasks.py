"""Task actions with the same scheduling semantics as the automation loop."""

from module.config.utils import filepath_args, read_file
from module.submodule.utils import get_available_func, get_available_mod, get_available_mod_func, get_config_mod, load_config
from module.webui.process_manager import ProcessManager
from module.webui.updater import updater


class TaskService:
    def run_now(self, config_name: str, task: str):
        """Queue a normal configured task for immediate scheduler execution."""
        tasks = read_file(filepath_args('args', 'nkas'))
        if task not in tasks:
            return None

        config = load_config(config_name)
        config.load()
        if not config.task_call(task, force_call=True):
            return None
        config.save()

        manager = ProcessManager.get_manager(config_name)
        started = False
        if not manager.alive:
            manager.start(get_config_mod(config_name), ev=updater.event)
            started = True
        return {'task': task, 'started_scheduler': started}

    def start_tool(self, config_name: str, task: str):
        available = set(get_available_func()) | set(get_available_mod()) | set(get_available_mod_func())
        if task not in available:
            return None
        manager = ProcessManager.get_manager(config_name)
        if manager.alive:
            return {'task': task, 'already_running': True}
        manager.start(task, ev=updater.event)
        return {'task': task, 'started': True}
