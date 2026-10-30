"""Synchronous checkpointers for hosted and local claim workflows."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from backend.config import agent_owner_path, checkpoint_owner_path, database_target
from backend.diagnostics import preserve_primary
from backend.ownership import DatabaseOwner


def checkpoint_path(path: Path) -> Path:
    return path


def configure(connection: Connection[DictRow]) -> None:
    available = connection.execute(
        "SELECT pg_try_advisory_lock_shared(70914001)"
    ).fetchone()
    if not available or not available["pg_try_advisory_lock_shared"]:
        raise RuntimeError("The product database is being rebuilt.")
    connection.execute("CREATE SCHEMA IF NOT EXISTS undercover")


@contextmanager
def service_lock(pool: ConnectionPool[Connection[DictRow]], enabled: bool):
    """Reserve service ownership from the same bounded checkpointer pool."""
    if not enabled:
        yield
        return
    connection = pool.getconn()
    try:
        row = connection.execute("SELECT pg_try_advisory_lock(70914004)").fetchone()
        if not row or not row["pg_try_advisory_lock"]:
            raise RuntimeError(
                "The agent service is already running for this database."
            )
        try:
            yield
        finally:
            with preserve_primary("Release agent service ownership"):
                connection.execute("SELECT pg_advisory_unlock(70914004)")
    finally:
        with preserve_primary("Return agent ownership connection"):
            pool.putconn(connection)


@contextmanager
def checkpointer(*, service_owner: bool = False) -> Iterator[BaseCheckpointSaver]:
    target = database_target()
    if isinstance(target, Path):
        with (
            DatabaseOwner(agent_owner_path(target)) if service_owner else nullcontext(),
            DatabaseOwner(checkpoint_owner_path(target)),
        ):
            connection = sqlite3.connect(
                checkpoint_path(target), check_same_thread=False
            )
            try:
                saver = SqliteSaver(connection)
                saver.setup()
                yield saver
            finally:
                with preserve_primary("Close claim checkpointer"):
                    connection.close()
    else:
        pool: ConnectionPool[Connection[DictRow]] = ConnectionPool(
            target.replace("postgresql+psycopg://", "postgresql://", 1),
            min_size=1,
            max_size=4,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "options": "-c search_path=undercover",
                "connect_timeout": 10,
            },
            configure=configure,
        )
        try:
            pool.open(wait=True, timeout=15)
            with service_lock(pool, service_owner):
                saver = PostgresSaver(pool)
                saver.setup()
                yield saver
        finally:
            with preserve_primary("Close claim checkpointer pool"):
                pool.close()
