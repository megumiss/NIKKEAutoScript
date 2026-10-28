"""自动推图运行依赖的按需安装，不参与主程序的常规依赖更新。"""

import importlib
import os
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlparse

from filelock import FileLock, Timeout
from packaging.requirements import Requirement

from module.config.config import TaskEnd
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.webui.setting import State

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / 'requirements-yolo.txt'
INSTALL_TIMEOUT = 600


def missing_requirements():
    """读取独立清单中的包及版本约束，返回当前解释器尚未满足的依赖。

    清单只接受普通包要求与注释，不展开其他 requirements 文件或 pip 选项；
    每次重新读取安装元数据，使等待其他实例安装的进程能够发现已完成的安装。
    """
    missing = []
    for line in REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        requirement = Requirement(line)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            missing.append(str(requirement))
            continue
        if installed not in requirement.specifier:
            missing.append(str(requirement))
    return missing


def check_stop(stop_event):
    """在等待安装锁或 pip 子进程时响应任务停止，不把主动停止记为安装失败。"""
    if stop_event is not None and stop_event.is_set():
        raise TaskEnd


def install_runtime(stop_event):
    """使用当前解释器安装独立运行依赖，沿用部署镜像并保持共享库版本。

    不调用主程序的依赖安装器，也不安装训练环境；NumPy、protobuf 和 packaging
    固定为当前已安装版本，避免推图依赖安装改变 OCR 等任务的运行环境。
    pip 最多运行十分钟，每秒检查停止请求，退出时回收子进程，Windows 不显示控制台。
    """
    deploy = State.deploy_config
    if not deploy.InstallDependencies:
        raise RuntimeError('自动安装依赖已禁用，请使用当前 Python 手动安装 requirements-yolo.txt。')
    constraints = ROOT / 'tmp' / 'campaign-runtime-constraints.txt'
    constraints.write_text(''.join(f'{name}=={version(name)}\n'
                                   for name in ('numpy', 'protobuf', 'packaging')), encoding='utf-8')
    args = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
            '--only-binary=:all:', '-r', str(REQUIREMENTS), '-c', str(constraints)]
    if deploy.PypiMirror:
        args.extend(['--index-url', deploy.PypiMirror])
        if deploy.PypiMirror.startswith('http:') or not deploy.SSLVerify:
            args.extend(['--trusted-host', urlparse(deploy.PypiMirror).hostname])
    elif not deploy.SSLVerify:
        args.extend(['--trusted-host', 'pypi.org', '--trusted-host', 'files.pythonhosted.org'])
    check_stop(stop_event)
    started = time.monotonic()
    last_report = started
    with subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as process:
        try:
            while True:
                check_stop(stop_event)
                elapsed = time.monotonic() - started
                if elapsed >= INSTALL_TIMEOUT:
                    raise TimeoutError(f'自动推图运行依赖安装超时：{INSTALL_TIMEOUT} 秒。')
                try:
                    output, _ = process.communicate(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - last_report >= 30:
                        logger.info(f'自动推图运行依赖安装中：已等待 {elapsed:.0f} 秒')
                        last_report = time.monotonic()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        for line in output.splitlines():
            if line.strip():
                logger.info(f'自动推图依赖安装：{line}')
        if process.returncode:
            raise RuntimeError(f'自动推图运行依赖安装失败：pip 退出码 {process.returncode}。')


def ensure_runtime(stop_event=None):
    """在自动推图观测前检查并按需安装独立依赖，普通任务不调用此入口。

    同一部署目录的多个实例通过文件锁串行安装，锁内复核以免重复下载。
    安装后必须成功导入 CPU 推理后端；损坏的环境、安装失败或锁等待超时均交给人工，
    不继续进入游戏识别。已加载的旧版推理库不原地替换，避免 Windows DLL 占用。
    """
    logger.hr('检查自动推图运行依赖', level=2)
    check_stop(stop_event)
    try:
        lock_path = ROOT / 'tmp' / 'campaign-runtime.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(lock_path))
        started = time.monotonic()
        while True:
            check_stop(stop_event)
            try:
                lock.acquire(timeout=1)
                break
            except Timeout:
                if time.monotonic() - started >= INSTALL_TIMEOUT:
                    raise TimeoutError(f'等待其他实例安装自动推图运行依赖超时：{INSTALL_TIMEOUT} 秒。')
        try:
            missing = missing_requirements()
            if missing:
                logger.info(f'自动推图运行依赖待安装：{", ".join(missing)}')
                if 'onnxruntime' in sys.modules:
                    raise RuntimeError('已加载的 ONNX Runtime 版本不符合要求，请重启任务后安装。')
                install_runtime(stop_event)
                importlib.invalidate_caches()
                if missing_requirements():
                    raise RuntimeError('pip 安装结束，但当前解释器仍缺少要求的自动推图运行依赖。')
            check_stop(stop_event)
            runtime = importlib.import_module('onnxruntime')
            if 'CPUExecutionProvider' not in runtime.get_available_providers():
                raise RuntimeError('ONNX Runtime 未提供自动推图需要的 CPU 推理后端。')
            logger.info(f'自动推图运行依赖已就绪：ONNX Runtime {runtime.__version__}')
        finally:
            lock.release()
    except (OSError, ValueError, ImportError, RuntimeError, subprocess.SubprocessError) as exc:
        logger.error(f'自动推图运行依赖准备失败：{exc}')
        logger.error(f'独立依赖清单：{REQUIREMENTS}；Python：{sys.executable}')
        raise RequestHumanTakeover from exc
