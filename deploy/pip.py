from functools import cached_property
import os
import sys

from deploy.config import DeployConfig
from module.exception import RequestHumanTakeover
from module.logger import logger


class PipManager(DeployConfig):
    @cached_property
    def python(self):
        exe = self.filepath("PythonExecutable")
        if os.path.exists(exe):
            return exe

        current = sys.executable.replace("\\", "/")
        logger.warning(f'PythonExecutable: {exe} does not exist, use current python instead: {current}')
        return current

    @cached_property
    def requirements_file(self):
        if self.RequirementsFile == 'requirements.txt':
            return 'requirements.txt'
        else:
            return self.filepath("RequirementsFile")

    @cached_property
    def pip(self):
        return f'"{self.python}" -m pip'

    def pip_install(self):
        logger.hr('Update Dependencies', 0)
        for key, value in vars(self).items():
            logger.info(f"{key} = {value}")
        
        logger.hr('Check Python', 1)
        self.execute(f'"{self.python}" --version')

        arg = ['--disable-pip-version-check']

        logger.hr('Update Dependencies', 1)
        arg = ' ' + ' '.join(arg) if arg else ''
        logger.hr('Update Dependencies', 2)  
        logger.info(f'{self.pip} install -r {self.requirements_file}{arg}')
        self.execute(f'{self.pip} install -r {self.requirements_file}{arg}')
