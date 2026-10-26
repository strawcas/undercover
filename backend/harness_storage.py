"""Additive SQLite migrations for the local harness."""

import sqlite3


def migrate(connection: sqlite3.Connection) -> None:
    """Add harness records without changing existing pilot work."""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(claims)")}
    for name, statement in (
        ("run_id", "ALTER TABLE claims ADD COLUMN run_id TEXT"),
        (
            "failed_attempts",
            "ALTER TABLE claims ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0",
        ),
        ("review_reason", "ALTER TABLE claims ADD COLUMN review_reason TEXT"),
    ):
        if name not in columns:
            connection.execute(statement)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS agent_runs "
        "(id TEXT PRIMARY KEY, body TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS agent_attempts (thread_id TEXT PRIMARY KEY, "
        "run_id TEXT NOT NULL, claim_id TEXT NOT NULL, "
        "body TEXT NOT NULL, finish_input TEXT)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS reference_sections (source TEXT, section_id TEXT, "
        "position INTEGER, parent_id TEXT, title TEXT, version TEXT, text TEXT, "
        "PRIMARY KEY(source, section_id))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, claim_id TEXT, "
        "thread_id TEXT, source TEXT, version TEXT, section_id TEXT, "
        "body TEXT NOT NULL, "
        "UNIQUE(claim_id, thread_id, source, version, section_id))"
    )
