import sys

from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager
from deploy.mirrorchyan import MirrorError
from deploy.source_update import run_update


class Installer(GitManager, PipManager):
    def install(self):
        try:
            run_update(self)
        except (ExecutionError, MirrorError) as e:
            print(str(e))
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
