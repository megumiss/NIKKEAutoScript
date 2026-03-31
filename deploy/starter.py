import json
import os
import subprocess
import sys
from datetime import datetime

from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.nkas import NKASManager
from deploy.pip import PipManager


class Starter(GitManager, PipManager, NKASManager):
    AUTO_UPDATE_NOTICE_PATH = './log/auto_update_notice.json'

    def _execute_output(self, command: str) -> str:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='utf8',
            shell=True,
        ).stdout.strip()

    def _get_head_commit(self):
        if not os.path.exists('./.git'):
            return None, None
        log = self._execute_output(f'"{self.git}" log -1 --pretty=format:"%H---%s"')
        if not log or '---' not in log:
            return None, None
        sha, message = log.split('---', maxsplit=1)
        return sha, message

    def _get_commit_messages(self, rev_range: str, limit: int = None):
        limit_arg = f' -{int(limit)}' if limit and int(limit) > 0 else ''
        log = self._execute_output(
            f'"{self.git}" log {rev_range} --pretty=format:"%h %s"{limit_arg}'
        )
        if not log:
            return []
        return [line.strip() for line in log.splitlines() if line.strip()]

    def _get_commit_count(self, rev_range: str) -> int:
        out = self._execute_output(f'"{self.git}" rev-list --count {rev_range}')
        try:
            return int(out)
        except Exception:
            return 0

    def _save_auto_update_notice(self, before_sha: str, after_sha: str):
        if not before_sha or not after_sha or before_sha == after_sha:
            return

        rev_range = f'{before_sha}..{after_sha}'
        payload = {
            'updated_at': datetime.now().isoformat(timespec='seconds'),
            'from_sha': before_sha[:8],
            'to_sha': after_sha[:8],
            'commit_count': self._get_commit_count(rev_range),
            'messages': self._get_commit_messages(rev_range),
        }
        os.makedirs(os.path.dirname(self.AUTO_UPDATE_NOTICE_PATH), exist_ok=True)
        with open(self.AUTO_UPDATE_NOTICE_PATH, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)

    def start(self):
        from deploy.atomic import atomic_failure_cleanup

        atomic_failure_cleanup('./config')
        try:
            if self.AutoUpdate:
                before_sha, _ = self._get_head_commit()
                self.git_update()
                self.pip_install()
                after_sha, _ = self._get_head_commit()
                self._save_auto_update_notice(before_sha, after_sha)
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
