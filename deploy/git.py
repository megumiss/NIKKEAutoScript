import os
import shutil
import subprocess
import urllib.request
from functools import cached_property

from deploy.config import DeployConfig, ExecutionError
from module.logger import logger


class GitManager(DeployConfig):
    @cached_property
    def git(self):
        return self.filepath('GitExecutable')

    @staticmethod
    def remove(file):
        try:
            os.remove(file)
            logger.info(f'Removed file: {file}')
        except FileNotFoundError:
            logger.info(f'File not found: {file}')

    def git_repository_init(self, repo, source='origin', branch='master', proxy=''):
        """
        初始化Git
        """
        logger.hr('Git Repository Init', 1)
        if not self.execute(f'"{self.git}" init', allow_failure=True):
            self.remove('./.git/config')
            self.remove('./.git/index')
            self.remove('./.git/HEAD')
            self.execute(f'"{self.git}" init')

        """
            设置代理
        """
        logger.hr('Set Git Proxy', 1)
        if proxy:
            self.execute(f'"{self.git}" config --local http.proxy {proxy}')
            self.execute(f'"{self.git}" config --local https.proxy {proxy}')
        else:
            self.execute(f'"{self.git}" config --local --unset http.proxy', allow_failure=True)
            self.execute(f'"{self.git}" config --local --unset https.proxy', allow_failure=True)

        """
            链接上游仓库
        """
        logger.hr('Set Git Repository', 1)
        if not self.execute(f'"{self.git}" remote set-url {source} {repo}', allow_failure=True):
            self.execute(f'"{self.git}" remote add {source} {repo}')

        """
            检查 nkas.exe 是否在版本控制中，如果在，则停止跟踪
        """
        try:
            # 如果文件在版本控制中，这条命令会成功
            self.execute(f'"{self.git}" ls-files --error-unmatch nkas.exe')
            logger.info('nkas.exe is tracked, removing from git tracking')
            self.execute(f'"{self.git}" rm --cached nkas.exe')
        except Exception:
            # 文件不在版本控制中，忽略
            logger.info('nkas.exe is not tracked, no action needed')

        """
            拉取最新上游仓库最新commit
        """
        logger.hr('Fetch Repository Branch', 1)
        self.execute(f'"{self.git}" fetch {source} {branch}')

        # Remove git lock
        lock_file = './.git/index.lock'
        if os.path.exists(lock_file):
            logger.info(f'Lock file {lock_file} exists, removing')
            os.remove(lock_file)

        """
            合并到本地
        """
        self.execute(f'"{self.git}" reset --hard {source}/{branch}')
        self.execute(f'"{self.git}" pull --ff-only {source} {branch}')

        logger.hr('Show Version', 1)
        self.execute(f'"{self.git}" --no-pager log --no-merges -1')

    def git_update(self):
        logger.hr('Update Repository', 0)

        # Check if git executable exists, if not, download and install
        if not os.path.exists(self.git):
            logger.info(f'Git executable not found: {self.git}')
            self.git_install()

        self.git_repository_init(
            repo=self.Repository,
            source='origin',
            branch=self.Branch,
            proxy=self.GitProxy,
        )

    def download_file(self, urls, target_path):
        """从多个镜像地址下载，直到成功"""
        for url in urls:
            logger.info(f'Trying to download: {url}')
            try:
                with urllib.request.urlopen(url, timeout=60) as resp, open(target_path, 'wb') as f:
                    shutil.copyfileobj(resp, f)
                logger.info(f'Download successful: {url}')
                return True
            except Exception as e:
                logger.error(f'Download failed: {url} - {e}')
        return False

    def extract_with_7za(self, sfx_file, output_dir):
        """调用 7za.exe 解压 SFX 文件"""
        seven_zip = os.path.abspath('toolkit/7z/7za.exe')
        if not os.path.exists(seven_zip):
            logger.error(f'Cannot find {seven_zip}, please ensure 7za.exe is placed in toolkit directory')
            raise FileNotFoundError(f'7za.exe not found at {seven_zip}')

        sfx_file = os.path.abspath(sfx_file)
        output_dir = os.path.abspath(output_dir)

        cmd = f'"{seven_zip}" x "{sfx_file}" -o"{output_dir}" -y'
        logger.info(f'Executing extraction command: {cmd}')
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f'Extraction failed: {result.stderr}')
                raise ExecutionError(f'Extraction failed: {result.stderr}')
        except Exception as e:
            logger.error(f'Extraction error: {e}')
            raise ExecutionError(f'Extraction error: {e}')

    def get_latest_portablegit_urls(self):
        """
        从清华源获取最新 PortableGit 版本号，并拼接清华源和 GitHub 的下载链接
        返回 [清华源URL, GitHubURL]，失败时返回 []
        """
        import re
        import urllib.request

        tsinghua_url = 'https://mirrors.tuna.tsinghua.edu.cn/github-release/git-for-windows/git/LatestRelease/'
        logger.info(f'Fetching latest release info from {tsinghua_url}')
        try:
            with urllib.request.urlopen(tsinghua_url, timeout=30) as resp:
                html = resp.read().decode('utf-8', errors='ignore')

            # 匹配 PortableGit-<version>-64-bit.7z.exe
            match = re.search(r'PortableGit-(\d+(?:\.\d+)+)-64-bit\.7z\.exe', html)
            if not match:
                logger.warning('Failed to detect latest PortableGit version from Tsinghua mirror')
                return []

            version = match.group(1)  # e.g. 2.51.0 / 2.51 / 2.51.0.1
            logger.info(f'Detected latest PortableGit version: {version}')

            # 清华源链接
            tuna_url = f'{tsinghua_url}PortableGit-{version}-64-bit.7z.exe'

            # GitHub 官方链接
            github_url = f'https://github.com/git-for-windows/git/releases/download/v{version}.windows.1/PortableGit-{version}-64-bit.7z.exe'

            return [tuna_url, github_url]
        except Exception as e:
            logger.error(f'Error while fetching Tsinghua mirror info: {e}')
            return []

    def git_install(self):
        logger.hr('Install Git', 0)

        # 如果 git 可执行文件已存在，直接跳过
        if os.path.exists(self.git):
            logger.info(f'Git executable already exists: {self.git}')
            return

        target_dir = os.path.abspath('./toolkit/Git')
        os.makedirs(target_dir, exist_ok=True)

        # 默认 URL 列表（回退用）
        urls = [
            'https://github.com/git-for-windows/git/releases/download/v2.50.1.windows.1/PortableGit-2.50.1-64-bit.7z.exe',
        ]

        # 尝试获取最新 PortableGit 的清华源和 GitHub 链接
        latest_urls = self.get_latest_portablegit_urls()
        if latest_urls:
            # 插到前两位
            urls = latest_urls + urls

        exe_path = os.path.abspath(os.path.join(target_dir, 'PortableGit.exe'))

        try:
            # 下载
            if not self.download_file(urls, exe_path):
                logger.error('All mirrors failed to download, please check your network connection.')
                raise ExecutionError('Failed to download Git')

            logger.info('Starting extraction of PortableGit...')
            self.extract_with_7za(exe_path, target_dir)

            logger.info(f'Git installed successfully to {target_dir}')
        finally:
            # 删除临时文件
            if os.path.exists(exe_path):
                try:
                    os.remove(exe_path)
                    logger.info(f'Removed temporary file: {exe_path}')
                except Exception as e:
                    logger.error(f'Failed to remove temporary file {exe_path}: {e}')
