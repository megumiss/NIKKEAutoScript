import os
import re
import shutil
import subprocess
import typing as t
from dataclasses import dataclass
from functools import cached_property
from urllib.parse import urlparse

from deploy.config import DeployConfig, ExecutionError
from module.logger import logger

PADDLE_CPU_PACKAGE = 'paddlepaddle'
PADDLE_GPU_PACKAGE = 'paddlepaddle-gpu'
PADDLE_GPU_INDEX = 'https://www.paddlepaddle.org.cn/packages/stable/{cuda}/'
# The Windows 3.1 wheels do not include SM 120, even in the cu129 index.
PADDLE_GPU_CU129_VERSION = '3.2.0'


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
    def ocr_use_gpu(self) -> bool:
        return str(getattr(self, 'OcrDevice', 'cpu')).strip().lower() == 'gpu'

    @cached_property
    def paddle_cuda_variant(self) -> str:
        return self._detect_cuda_variant()

    @cached_property
    def requirements_file_effective(self):
        """
        GPU 模式下 pip 不认识 paddlepaddle-gpu 对 paddlepaddle 的替代关系，
        -r 安装前需生成一份去掉 paddlepaddle 行的临时文件，由 _paddle_prepare 单独安装 GPU 版
        """
        if not self.ocr_use_gpu:
            return self.requirements_file
        with open(self.requirements_file, 'r', encoding='utf-8') as f:
            lines = [line for line in f if not re.match(rf'^{PADDLE_CPU_PACKAGE}==', line.strip())]
        target = './tmp/requirements-gpu-runtime.txt'
        os.makedirs('./tmp', exist_ok=True)
        with open(target, 'w', encoding='utf-8', newline='') as f:
            f.writelines(lines)
        return target

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
        # pip does not treat paddlepaddle-gpu as satisfying the CPU package pin.
        if self.ocr_use_gpu:
            gpu_version = PADDLE_GPU_CU129_VERSION if self.paddle_cuda_variant == 'cu129' else None
            data = [
                DataDependency(name=PADDLE_GPU_PACKAGE, version=gpu_version or dep.version)
                if dep.name == PADDLE_CPU_PACKAGE else dep
                for dep in data
            ]
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

    def _detect_cuda_variant(self) -> str:
        """根据 PaddleCuda 配置或 nvidia-smi 上报的驱动 CUDA 上限选择 paddlepaddle-gpu 变体"""
        choice = str(getattr(self, 'PaddleCuda', 'auto')).strip().lower()
        if choice in ('cu118', 'cu126', 'cu129'):
            return choice
        exe = shutil.which('nvidia-smi')
        for candidate in (r'C:\Windows\System32\nvidia-smi.exe', r'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe'):
            if exe:
                break
            if os.path.exists(candidate):
                exe = candidate
        if not exe:
            raise ExecutionError(
                'OcrDevice is gpu but nvidia-smi was not found; '
                'install an NVIDIA driver or set PaddleCuda to cu129/cu126/cu118 manually'
            )
        result = subprocess.run([exe], capture_output=True, text=True)
        match = re.search(r'CUDA(?: UMD)? Version:\s*(\d+)\.(\d+)', result.stdout)
        if not match:
            raise ExecutionError(f'Failed to parse CUDA Version from nvidia-smi output: {result.stdout.strip()[:200]}')
        major, minor = int(match.group(1)), int(match.group(2))
        if (major, minor) >= (12, 9):
            variant = 'cu129'
        elif (major, minor) >= (12, 6):
            variant = 'cu126'
        elif (major, minor) >= (11, 8):
            variant = 'cu118'
        else:
            raise ExecutionError(
                f'NVIDIA driver supports CUDA {major}.{minor} at most, paddlepaddle-gpu requires >= 11.8; '
                'please upgrade the driver'
            )
        logger.info(f'Detected driver CUDA {major}.{minor}, use paddlepaddle-gpu {variant}')
        return variant

    def _paddle_prepare(self):
        """paddlepaddle 与 paddlepaddle-gpu 提供同名 paddle 模块、互斥，切换设备时先卸载另一个再装目标版本"""
        required = {dep.name: dep for dep in self.set_required_dependency}
        installed = {dep.name for dep in self.set_installed_dependency}
        target = PADDLE_GPU_PACKAGE if self.ocr_use_gpu else PADDLE_CPU_PACKAGE
        other = PADDLE_CPU_PACKAGE if self.ocr_use_gpu else PADDLE_GPU_PACKAGE
        dep = required.get(target)
        if dep is None:
            logger.warning(f'{PADDLE_CPU_PACKAGE} pin not found in {self.requirements_file}, skip Paddle install')
            return
        if dep in self.set_installed_dependency and other not in installed:
            return

        # Check the driver before removing a working CPU installation.
        index = PADDLE_GPU_INDEX.format(cuda=self.paddle_cuda_variant) if self.ocr_use_gpu else self.PypiMirror
        if other in installed:
            logger.hr(f'Uninstall {other}', 1)
            self.execute(f'{self.pip} uninstall -y {other}')
        arg = f' -i {index}' if index else ''
        if index and ('http:' in index or not self.SSLVerify):
            arg += f' --trusted-host {urlparse(index).hostname}'
        # Both distributions own paddle/*; uninstalling either removes shared files.
        if dep in self.set_installed_dependency:
            arg += ' --force-reinstall --no-deps'
        logger.hr(f'Install {dep.pretty_name}', 1)
        self.execute(f'{self.pip} install {dep.pretty_name}{arg} --disable-pip-version-check')
        self.__dict__.pop('set_installed_dependency', None)
        self.__dict__.pop('set_dependency_to_install', None)

    def pip_install(self):
        logger.hr('Update Dependencies', 0)

        if not self.InstallDependencies:
            logger.info('InstallDependencies is disabled, skip')
            return

        self._paddle_prepare()
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
            self.execute(f'{self.pip} install -r {self.requirements_file_effective}{arg}')
        except ExecutionError:
            logger.error('Failed to install dependencies')
            raise
        except Exception as e:
            logger.error(f'Unexpected error during pip install: {e}')
            raise ExecutionError(f'Pip install failed: {e}')
