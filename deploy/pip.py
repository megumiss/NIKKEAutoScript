import os
import re
import shutil
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
        # 1. 去除 extras (如 uvicorn[standard])
        self.name = re.sub(r'\[.*\]', '', self.name)

        # 2. 深度规范化包名 (PEP 503)
        # 将所有 ., _, - 替换为单个 -，并转小写
        # 例子: "ruamel.yaml" -> "ruamel-yaml", "Ruamel_Yaml" -> "ruamel-yaml"
        self.name = re.sub(r'[-_.]+', '-', self.name).lower().strip()

        # 3. 版本号规范化
        self.version = self.version.strip()
        # 去除可能存在的 v 前缀 (例如 v0.18.14 -> 0.18.14)
        if self.version.lower().startswith('v'):
            self.version = self.version[1:]
        # 去除末尾的 .0
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
        # 确保路径分隔符统一
        return os.path.abspath(os.path.join(self.python, '../Lib/site-packages')).replace(r'\\', '/').replace('\\', '/')

    @cached_property
    def set_installed_dependency(self) -> t.Set[DataDependency]:
        data = []
        # 1. ^(.*?)- : 非贪婪匹配包名，直到遇到最后一个分隔符
        # 2. ((?:\d|v).*) : 版本号部分，允许以数字 (\d) 或字母 v 开头
        # 3. \.(?:dist|egg)-info$ : 支持 .dist-info 和 .egg-info 两种后缀
        regex = re.compile(r'^(.*?)-((?:\d|v).*?)\.(?:dist|egg)-info$', re.IGNORECASE)

        try:
            # 获取目录列表
            file_list = os.listdir(self.python_site_packages)
            for name in file_list:
                res = regex.search(name)
                if res:
                    raw_name = res.group(1)
                    raw_version = res.group(2)

                    dep = DataDependency(name=raw_name, version=raw_version)
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
        # requirements.txt 正则
        regex = re.compile(r'^([^#\s]+)==([^#\s]+)')
        file = self.requirements_file
        try:
            with open(file, 'r', encoding='utf-8') as f:
                for line in f.readlines():
                    line = line.strip()
                    if not line:
                        continue
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
        Compare required vs installed using normalized DataDependency objects
        """
        data = []
        installed_set = self.set_installed_dependency

        for dep in self.set_required_dependency:
            if dep not in installed_set:
                data.append(dep)
        return set(data)

    @cached_property
    def pip(self):
        return f'"{self.python}" -m pip'

    def pip_install(self):
        logger.hr('Check nkas.exe', 0)
        nkas_path = './nkas.exe'
        nkas_source = './deploy/build/nkas.exe'
        if not os.path.exists(nkas_path):
            if os.path.exists(nkas_source):
                logger.info(f'{nkas_path} not found, copying from {nkas_source}')
                shutil.copy(nkas_source, nkas_path)
            else:
                logger.warning(f'{nkas_source} does not exist, cannot copy nkas.exe')

        logger.hr('Update Dependencies', 0)

        if not self.InstallDependencies:
            logger.info('InstallDependencies is disabled, skip')
            return

        deps_to_install = self.set_dependency_to_install
        if not len(deps_to_install):
            logger.info('All dependencies installed')
            return
        else:
            logger.info(f'Dependencies to install: {deps_to_install}')

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
