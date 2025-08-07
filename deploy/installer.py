from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager


class Installer(GitManager, PipManager):
    def install(self):
        try:
            self.git_install()
            # self.alas_kill()
            self.pip_install()
            # self.app_update()
            # self.adb_install()
        except ExecutionError:
            exit(1)

if __name__ == '__main__':
    Installer().install()
