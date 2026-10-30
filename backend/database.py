"""Shared SQLAlchemy Core tables, parameter binding and result mappings."""

from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.engine import Connection as SAConnection
from sqlalchemy.engine import CursorResult

metadata = MetaData()
usage = Table(
    "attempt_usage",
    metadata,
    Column("thread_id", Text, primary_key=True),
    Column("request_id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
)
records = Table(
    "records",
    metadata,
    Column("kind", Text, primary_key=True),
    Column("id", Text, primary_key=True),
    Column("body", Text, nullable=False),
)
claims = Table(
    "claims",
    metadata,
    Column("id", Text, primary_key=True),
    Column("state", Text, nullable=False),
    Column("thread_id", Text),
    Column("active", Integer, nullable=False, server_default="0"),
    Column("decision", Text),
    Column("payout_cents", Integer),
    Column("sign_off", Text),
    Column("payment", Text),
    Column("queue_position", Integer),
    Column("run_id", Text),
    Column("failed_attempts", Integer, nullable=False, server_default="0"),
    Column("review_reason", Text),
)
events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", Text, nullable=False),
    Column("thread_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("detail", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
runs = Table(
    "agent_runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("body", Text, nullable=False),
    Column("position", Integer, nullable=False),
)
attempts = Table(
    "agent_attempts",
    metadata,
    Column("thread_id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("claim_id", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("finish_input", Text),
    Column("position", Integer, nullable=False),
)
sections = Table(
    "reference_sections",
    metadata,
    Column("source", Text, primary_key=True),
    Column("section_id", Text, primary_key=True),
    Column("position", Integer),
    Column("parent_id", Text),
    Column("title", Text),
    Column("version", Text),
    Column("text", Text),
)
evidence = Table(
    "evidence",
    metadata,
    Column("id", Text, primary_key=True),
    Column("claim_id", Text),
    Column("thread_id", Text),
    Column("source", Text),
    Column("version", Text),
    Column("section_id", Text),
    Column("body", Text, nullable=False),
    Column("position", Integer, nullable=False),
    UniqueConstraint("claim_id", "thread_id", "source", "version", "section_id"),
)


class Row(dict[str, Any]):
    """A database boundary mapping with scalar indexing for existing callers."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class Result:
    def __init__(self, cursor: CursorResult):
        self.cursor = cursor

    def fetchone(self) -> Any:
        row = self.cursor.mappings().fetchone()
        return Row(row) if row is not None else None

    def fetchall(self) -> list[Row]:
        return [Row(row) for row in self.cursor.mappings().fetchall()]

    def __iter__(self) -> Iterator[Row]:
        return iter(self.fetchall())


class Connection:
    """Bind positional values once using Core's portable named parameters."""

    def __init__(self, raw: SAConnection):
        self.raw = raw

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> Result:
        parts = sql.split("?")
        if len(parts) != len(parameters) + 1:
            raise ValueError("SQL parameter count does not match its bindings.")
        statement = "".join(
            part + (f":p{index}" if index < len(parameters) else "")
            for index, part in enumerate(parts)
        )
        return Result(
            self.raw.execute(
                text(statement), {f"p{i}": value for i, value in enumerate(parameters)}
            )
        )

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def next_position(self, table: Table) -> int:
        return (self.raw.scalar(select(func.max(table.c.position))) or 0) + 1
