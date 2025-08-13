import os
import re
import typing as t
from dataclasses import dataclass
from functools import cached_property
from urllib.parse import urlparse

from deploy.config import DeployConfig, ExecutionError
from module.logger import logger


@dataclass
class DataDependency:
    name: str
    version: str

    def __post_init__(self):
        # uvicorn[standard] -> uvicorn
        self.name = re.sub(r'\[.*\]', '', self.name)
        # opencv_python -> opencv-python
        self.name = self.name.replace('_', '-').strip()
        # PyYaml -> pyyaml
        self.name = self.name.lower()
        self.version = self.version.strip()
        self.version = re.sub(r'\.0$', '', self.version)

    @cached_property
    def pretty_name(self):
        return f'{self.name}=={self.version}'

    def __str__(self):
        return self.pretty_name

    __repr__ = __str__

    def __eq__(self, other):
        return str(self) == str(other)

    def __hash__(self):
        return hash(str(self))


class PipManager(DeployConfig):
    @cached_property
    def python(self):
        return self.filepath('PythonExecutable')

    @cached_property
    def requirements_file(self):
        if self.RequirementsFile == 'requirements.txt':
            return 'requirements.txt'
        else:
            return self.filepath('RequirementsFile')

    @cached_property
    def python_site_packages(self):
        return os.path.abspath(os.path.join(self.python, '../Lib/site-packages')).replace(r'\\', '/').replace('\\', '/')

    @cached_property
    def set_installed_dependency(self) -> t.Set[DataDependency]:
        data = []
        regex = re.compile(r'(.*)-(.*).dist-info')
        try:
            for name in os.listdir(self.python_site_packages):
                res = regex.search(name)
                if res:
                    dep = DataDependency(name=res.group(1), version=res.group(2))
                    data.append(dep)
        except FileNotFoundError:
            logger.info(f'Directory not found: {self.python_site_packages}')
        except PermissionError:
            logger.error(f'Permission denied accessing: {self.python_site_packages}')
        except Exception as e:
            logger.error(f'Error reading site-packages: {e}')
        return set(data)

    @cached_property
    def set_required_dependency(self) -> t.Set[DataDependency]:
        data = []
        regex = re.compile(r'^([^#\s]+)==([^#\s]+)')
        file = self.filepath('RequirementsFile')
        try:
            with open(file, 'r', encoding='utf-8') as f:
                for line in f.readlines():
                    res = regex.search(line)
                    if res:
                        dep = DataDependency(name=res.group(1), version=res.group(2))
                        data.append(dep)
        except FileNotFoundError:
            logger.info(f'File not found: {file}')
        except Exception as e:
            logger.error(f'Error reading requirements file: {e}')
        return set(data)

    @cached_property
    def set_dependency_to_install(self) -> t.Set[DataDependency]:
        """
        A poor dependency comparison, but much much faster than `pip install` and `pip list`
        """
        data = []
        for dep in self.set_required_dependency:
            if dep not in self.set_installed_dependency:
                data.append(dep)
        return set(data)

    @cached_property
    def pip(self):
        return f'"{self.python}" -m pip'

    def pip_install(self):
        logger.hr('Update Dependencies', 0)

        if not self.InstallDependencies:
            logger.info('InstallDependencies is disabled, skip')
            return

        if not len(self.set_dependency_to_install):
            logger.info('All dependencies installed')
            return
        else:
            logger.info(f'Dependencies to install: {self.set_dependency_to_install}')

        logger.hr('Check Python', 1)
        self.execute(f'"{self.python}" --version')

        arg = []
        if self.PypiMirror:
            mirror = self.PypiMirror
            arg += ['-i', mirror]
            # Trust http mirror or skip ssl verify
            if 'http:' in mirror or not self.SSLVerify:
                arg += ['--trusted-host', urlparse(mirror).hostname]
        elif not self.SSLVerify:
            arg += ['--trusted-host', 'pypi.org']
            arg += ['--trusted-host', 'files.pythonhosted.org']
        arg += ['--disable-pip-version-check']

        logger.hr('Update Dependencies', 1)
        arg = ' ' + ' '.join(arg) if arg else ''
        try:
            self.execute(f'{self.pip} install -r {self.requirements_file}{arg}')
        except ExecutionError:
            logger.error('Failed to install dependencies')
            raise
        except Exception as e:
            logger.error(f'Unexpected error during pip install: {e}')
            raise ExecutionError(f'Pip install failed: {e}')
