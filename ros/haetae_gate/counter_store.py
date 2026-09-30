"""Owner-only, exclusive, durable input counter reservations."""
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile


class CounterStore:
    def __init__(self, target):
        self.target = Path(target)
        info = self.target.parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("counter directory must be owned by this user and owner-only")
        if self.target.exists() or self.target.is_symlink():
            info = self.target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("counter file must be owned by this user and owner-only")
        self.lock = os.open(str(self.target) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.lock)
            raise

    def reserve(self, epoch, counters):
        descriptor, temporary = tempfile.mkstemp(dir=self.target.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"auth_epoch": epoch, "counters": counters}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.target)
            directory = os.open(self.target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def close(self):
        os.close(self.lock)
