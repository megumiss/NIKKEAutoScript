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
        # 去除 extra 依赖标识，例如: uvicorn[standard] -> uvicorn
        self.name = re.sub(r'\[.*\]', '', self.name)

        # 将所有的 ., _, - 统一替换为 -，并转为小写。
        self.name = re.sub(r'[-_.]+', '-', self.name).lower().strip()

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
        # ^(.*?): 非贪婪匹配包名
        # -: 分隔符
        # (\d.*?): 版本号 (强制要求数字开头，防止包名里的连字符干扰)
        # \.dist-info$: 严格匹配后缀
        regex = re.compile(r'^(.*?)-(\d.*?)\.dist-info$')

        try:
            for name in os.listdir(self.python_site_packages):
                res = regex.search(name)
                if res:
                    # 获取到的原始名字传入 DataDependency 后会被自动规范化
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
        # requirements.txt 解析正则
        regex = re.compile(r'^([^#\s]+)==([^#\s]+)')
        file = self.requirements_file  # 使用 property 获取路径
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
        # 由于 DataDependency 实现了规范化，这里可以直接使用集合运算或比较
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

        # 这里的检查逻辑现在更加准确了
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
