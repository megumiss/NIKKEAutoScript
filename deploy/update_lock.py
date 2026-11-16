import os
from pathlib import Path

from deploy.config import ExecutionError


class UpdateBusyError(ExecutionError):
    pass


class UpdateLock:
    def __init__(self, root):
        root = Path(root).resolve()
        self.path = root / 'tmp' / 'update.lock'
        if not self.path.parent.resolve().is_relative_to(root):
            raise UpdateBusyError('更新锁目录越界。')
        self.stream = None

    def acquire(self):
        if self.stream is not None:
            return
        stream = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.path.open('a+b')
            if os.name == 'nt':
                import msvcrt
                if not self.path.stat().st_size:
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            stream.seek(0)
            stream.truncate()
            stream.write(str(os.getpid()).encode('ascii'))
            stream.flush()
            self.stream = stream
        except OSError:
            if stream is not None:
                stream.close()
            raise UpdateBusyError('另一个更新正在运行，或更新锁不可写。') from None

    def release(self):
        if self.stream is None:
            return
        try:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None

    @classmethod
    def busy(cls, root):
        lease = cls(root)
        if not lease.path.exists():
            return False
        try:
            lease.acquire()
        except UpdateBusyError:
            return True
        else:
            lease.release()
            return False
