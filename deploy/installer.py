import sys

from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager


class Installer(GitManager, PipManager):
    def install(self):
        try:
            self.git_install()
            self.git_init()
            # self.alas_kill()
            self.pip_install()
            # self.app_update()
            # self.adb_install()
        except ExecutionError:
            input('Press Enter to continue...')  # Keep window open
            sys.exit(1)
        except Exception as e:
            print(f'Unexpected error: {e}')
            input('Press Enter to continue...')  # Keep window open
            sys.exit(1)


if __name__ == '__main__':
    try:
        Installer().install()
    except Exception as e:
        print(f'Installation failed: {e}')
        input('Press Enter to continue...')  # Keep window open
        sys.exit(1)
