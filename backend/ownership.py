"""An operating-system lock held for a database owner's lifetime."""

import sys
from pathlib import Path
from typing import BinaryIO, Self

from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend.diagnostics import preserve_primary

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
        with preserve_primary("Release file database ownership"):
            self.release()


class PostgresOwner:
    """Share maintenance protection, but permit only one backend owner."""

    def __init__(self, engine: Engine, *, maintenance: bool = False):
        self.engine = engine
        self.maintenance = maintenance
        self.connection = None

    def acquire(self) -> Self:
        connection = self.engine.connect()
        self.connection = connection
        function = (
            "pg_try_advisory_lock"
            if self.maintenance
            else "pg_try_advisory_lock_shared"
        )
        try:
            available = connection.scalar(text(f"SELECT {function}(70914001)"))
            if available and not self.maintenance:
                available = connection.scalar(
                    text("SELECT pg_try_advisory_lock(70914003)")
                )
            if not available:
                raise RuntimeError(
                    "Database is already in use. Stop its backend and agent "
                    "before rebuilding."
                )
            connection.commit()
        except BaseException:
            with preserve_primary("Release failed database ownership"):
                self.release()
            raise
        return self

    def release(self) -> None:
        if self.connection is not None:
            try:
                self.connection.execute(text("SELECT pg_advisory_unlock_all()"))
                self.connection.commit()
            finally:
                with preserve_primary("Close database ownership connection"):
                    self.connection.close()
                    self.connection = None

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, *_error) -> None:
        with preserve_primary("Release Postgres database ownership"):
            self.release()
