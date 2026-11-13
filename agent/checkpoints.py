"""One bounded process pool, with independent savers for claim and round graphs."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection, sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

SCHEMA = "undercover"
CONNECTIONS = 4


@dataclass
class Checkpoints:
    pool: ConnectionPool[Connection[DictRow]] | None = None


state = Checkpoints()


def new_saver() -> PostgresSaver:
    """Allocate a saver that shares the process pool, without another pool."""
    if state.pool is None:
        raise RuntimeError("The checkpoint pool is not open")
    return PostgresSaver(state.pool)


def clear_threads(thread_ids: list[str]) -> None:
    """Discard evaluation checkpoints while retaining backend evidence."""
    saver = new_saver()
    for thread_id in thread_ids:
        saver.delete_thread(thread_id)


@contextmanager
def checkpointer() -> Iterator[PostgresSaver]:
    """Keep at most four connections open in the agent's private schema."""
    options = {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": f"-c search_path={SCHEMA}",
        "application_name": f"undercover-agent-{os.getpid()}",
    }
    with ConnectionPool[Connection[DictRow]](
        os.environ["DATABASE_URL"], min_size=1, max_size=CONNECTIONS, kwargs=options
    ) as pool:
        with pool.connection() as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA))
            )
        saver = PostgresSaver(pool)
        saver.setup()
        state.pool = pool
        try:
            yield saver
        finally:
            state.pool = None
