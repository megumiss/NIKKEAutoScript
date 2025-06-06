import copy
import os
from functools import cached_property
from typing import Optional

from deploy.utils import DEPLOY_CONFIG, poor_yaml_read, DEPLOY_TEMPLATE, poor_yaml_write
from module.logger import logger


class ExecutionError(Exception):
    pass


class ConfigModel:
    Repository: str = "https://github.com/megumiss/NIKKEAutoScript"
    Branch: str = "master"
    GitExecutable: str = "./toolkit/Git/mingw64/bin/git.exe"
    GitProxy: Optional[str] = None

    PythonExecutable: str = "./toolkit/python.exe"
    RequirementsFile: str = "requirements.txt"

    AdbExecutable: str = "./toolkit/android-platform-tools/adb.exe"
    ReplaceAdb: bool = True
    AutoConnect: bool = True
    InstallUiautomator2: bool = True

    EnableReload: bool = True
    CheckUpdateInterval: int = 5
    AutoRestartTime: str = "03:50"

    WebuiHost: str = "localhost"
    WebuiPort: int = 12271


class DeployConfig(ConfigModel):
    def __init__(self, file=DEPLOY_CONFIG):
        """
        Args:
            file (str): User deploy config.
        """
        self.file = file
        self.config = {}
        self.read()
        self.write()
        self.show_config()

    def read(self):
        self.config = poor_yaml_read(DEPLOY_TEMPLATE)
        self.config_template = copy.deepcopy(self.config)
        '''
            现有配置 ./config/deploy.yaml
        '''
        self.config.update(poor_yaml_read(self.file))

        '''
            将配置写入类变量
        '''
        for key, value in self.config.items():
            if hasattr(self, key):
                super().__setattr__(key, value)

    def write(self):
        poor_yaml_write(self.config, self.file)

    def show_config(self):
        logger.hr("Show deploy config", 1)
        for k, v in self.config.items():
            if self.config_template[k] == v:
                continue

        logger.info(f"Rest of the configs are the same as default")

    def filepath(self, key):
        """
        Args:
            key (str):

        Returns:
            str: Absolute filepath.
        """
        return (
            os.path.abspath(os.path.join(self.root_filepath, self.config[key]))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    @cached_property
    def root_filepath(self):
        return (
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    def execute(self, command, allow_failure=False, output=True):
        """
        Args:
            command (str):
            allow_failure (bool):
            output(bool):

        Returns:
            bool: If success.
                Terminate installation if failed to execute and not allow_failure.
        """
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        if not output:
            command = command + ' >nul 2>nul'
        logger.info(command)
        error_code = os.system(command)
        if error_code:
            if allow_failure:
                logger.info(f"[ allowed failure ], error_code: {error_code}")
                return False
            else:
                logger.info(f"[ failure ], error_code: {error_code}")
                self.show_error(command)
                raise ExecutionError
        else:
            logger.info(f"[ success ]")
            return True

    def show_error(self, command=None):
        logger.hr("Update failed", 0)
        self.show_config()
        logger.info("")
        logger.info(f"Last command: {command}")
        logger.info(
            "Please check your deploy settings in config/deploy.yaml "
        )
        logger.info("Take the screenshot of entire window if you need help")
