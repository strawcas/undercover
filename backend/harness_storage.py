"""Additive migrations preserving saved business records and ordering."""

from sqlalchemy import inspect, literal_column, select

from backend.database import Connection, metadata


def migrate(connection: Connection) -> None:
    """Create portable tables and add columns missing in older SQLite databases."""
    metadata.create_all(connection.raw)
    for table_name, columns in {
        "claims": {
            "queue_position": "INTEGER",
            "run_id": "TEXT",
            "failed_attempts": "INTEGER NOT NULL DEFAULT 0",
            "review_reason": "TEXT",
            "decision_request": "TEXT",
        },
        "agent_runs": {"position": "INTEGER"},
        "agent_attempts": {"position": "INTEGER"},
        "evidence": {"position": "INTEGER"},
    }.items():
        existing = {
            column["name"] for column in inspect(connection.raw).get_columns(table_name)
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"
                )
    for table, key in [
        ("agent_runs", "id"),
        ("agent_attempts", "thread_id"),
        ("evidence", "id"),
    ]:
        definition = metadata.tables[table]
        order = (
            literal_column("rowid")
            if connection.raw.dialect.name == "sqlite"
            else definition.c[key]
        )
        ids = (
            connection.raw.execute(
                select(definition.c[key])
                .where(definition.c.position.is_(None))
                .order_by(order)
            )
            .scalars()
            .all()
        )
        for position, identifier in enumerate(ids, 1):
            connection.raw.execute(
                definition.update()
                .where(definition.c[key] == identifier)
                .values(position=position)
            )
