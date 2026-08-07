"""Shared validation and read helpers for API routes."""

from module.config.utils import nkas_instance
from module.submodule.utils import load_config
from module.webui.process_manager import ProcessManager


class InstanceNotFound(Exception):
    pass


def validate_instance(name: str) -> None:
    if name not in nkas_instance():
        raise InstanceNotFound(f'Instance "{name}" not found.')


def get_manager(name: str) -> ProcessManager:
    validate_instance(name)
    return ProcessManager.get_manager(name)


def load_instance_config(name: str):
    validate_instance(name)
    return load_config(name)
