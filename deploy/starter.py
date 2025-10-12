import sys

from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.nkas import NKASManager
from deploy.pip import PipManager


class Starter(GitManager, PipManager, NKASManager):
    def start(self):
        from deploy.atomic import atomic_failure_cleanup

        atomic_failure_cleanup('./config')
        try:
            if self.AutoUpdate:
                self.git_update()
                self.pip_install()
            self.nkas_kill()
        except ExecutionError:
            input('Press Enter to continue...')  # Keep window open
            sys.exit(1)
        except Exception as e:
            print(f'Unexpected error: {e}')
            input('Press Enter to continue...')  # Keep window open
            sys.exit(1)


if __name__ == '__main__':
    try:
        Starter().start()
    except Exception as e:
        print(f'Start failed: {e}')
        input('Press Enter to continue...')  # Keep window open
        sys.exit(1)
