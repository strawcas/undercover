"""An operating-system lock held for a database owner's lifetime."""

import sys
from pathlib import Path
from typing import BinaryIO, Self

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class DatabaseOwner:
    """Prevent another backend, queue command or rebuild using this database."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.file: BinaryIO | None = None

    def acquire(self) -> Self:
        lock = self.path.with_name(self.path.name + ".lock").open("a+b")
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if sys.platform == "win32":
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            lock.close()
            raise RuntimeError(
                f"Database is already in use: {self.path}. Stop its backend or command."
            ) from error
        self.file = lock
        return self

    def release(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, *_error) -> None:
        self.release()
