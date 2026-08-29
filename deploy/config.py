import copy
import os
from functools import cached_property
from typing import Optional, Union

from deploy.utils import DEPLOY_CONFIG, DEPLOY_TEMPLATE, poor_yaml_read, poor_yaml_write
from module.logger import logger


class ExecutionError(Exception):
    def __init__(self, message='', command=None, error_code=None):
        if not message and command is not None and error_code is not None:
            message = f'{command} failed with exit code {error_code}'
        super().__init__(message)
        self.command = command
        self.error_code = error_code


class ConfigModel:
    Repository: str = "https://github.com/megumiss/NIKKEAutoScript"
    Branch: str = "master"
    GitExecutable: str = "./toolkit/Git/mingw64/bin/git.exe"
    GitProxy: Optional[str] = None
    SSLVerify: bool = True
    AutoUpdate: bool = True

    PythonExecutable: str = "./toolkit/python.exe"
    PypiMirror: Optional[str] = None
    InstallDependencies: bool = True
    RequirementsFile: str = "requirements.txt"

    AdbExecutable: str = "./toolkit/Lib/site-packages/adbutils/binaries/adb.exe"
    ReplaceAdb: bool = True
    AutoConnect: bool = True
    InstallUiautomator2: bool = True

    EnableReload: bool = True
    CheckUpdateInterval: int = 5
    AutoRestartTime: str = "03:50"
    DesktopUpdateManifest: str = (
        "https://nkas.megumiss.top/releases/latest/nkas-desktop.json"
    )

    WebuiHost: str = "0.0.0.0"
    WebuiPort: int = 12271
    ConsoleEnabled: bool = False
    ConsoleAllowHosts: str = '127.0.0.1, localhost, ::1'
    DpiScaling: bool = True
    HardwareAcceleration: bool = False

    Language: str = "zh-CN"
    Theme: str = "light"
    HomePage: str = "overview"
    Password: Optional[str] = None
    CDN: Union[str, bool] = False
    Run: Optional[str] = None
    ReadNoticeIds: Optional[str] = None

    # Serial execution
    SerialEnable: bool = False
    SerialGroup: Optional[str] = None
    SerialOnError: str = "skip"
    SerialIdleThreshold: int = 5

    # Statistics
    EnableStatistics: bool = True

    # Log
    LogRetentionDays: int = 30

    # Remote Access
    EnableRemoteAccess: bool = False
    SSHUser: Optional[str] = None
    SSHServer: Optional[str] = None
    SSHExecutable: Optional[str] = None

    # Dynamic
    GitOverCdn: bool = False


class DeployConfig(ConfigModel):
    def __init__(self, file=DEPLOY_CONFIG):
        """
        Args:
            file (str): User deploy config.
        """
        self.file = file
        self.config = {}
        self.read()
        # deploy.yaml 是跨进程共享的程序级配置（启动器、Web UI 工作进程、
        # 实例进程都会实例化 DeployConfig）。构造时无条件 write() 会让每个
        # 进程用各自的快照全量重写文件：启动瞬间多进程并发重写互相覆盖，
        # 陈旧快照（如 ReadNoticeIds 在持久化之前读到的 null）会把别人刚
        # 写入的值冲掉。因此仅在文件不存在时创建默认文件，真正的修改统一
        # 走 DeployConfig.__setattr__（module/webui/config.py）显式落盘。
        if not os.path.exists(self.file):
            self.write()
        self.show_config()

    def read(self):
        self.config = poor_yaml_read(DEPLOY_TEMPLATE)
        self.config_template = copy.deepcopy(self.config)
        """
            现有配置 ./config/deploy.yaml
        """
        self.config.update(poor_yaml_read(self.file))

        """
            将配置写入类变量
        """
        for key, value in self.config.items():
            if hasattr(self, key):
                super().__setattr__(key, value)

    def write(self):
        poor_yaml_write(self.config, self.file)

    def show_config(self):
        logger.hr("Show deploy config", 1)
        for k, v in self.config.items():
            # User config may carry keys dropped from the template (e.g. after
            # an update); missing keys count as changed and get logged.
            if self.config_template.get(k) == v:
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
                raise ExecutionError(command=command, error_code=error_code)
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
