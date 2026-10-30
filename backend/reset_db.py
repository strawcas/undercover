"""Explicitly replace the local database with a validated fresh source queue."""

import os
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp

from sqlalchemy import inspect, table
from sqlalchemy.exc import SQLAlchemyError

from backend.config import (
    ROOT,
    agent_owner_path,
    checkpoint_owner_path,
    database_target,
    load_environment,
)
from backend.database import metadata
from backend.diagnostics import preserve_primary
from backend.harness_storage import migrate
from backend.ownership import DatabaseOwner
from backend.storage import Store


def rebuild(path: Path, data_dir: Path = ROOT / "data") -> int:
    """Rebuild only the selected database, preserving it if source validation fails."""
    path = path.resolve()
    with (
        DatabaseOwner(path),
        DatabaseOwner(agent_owner_path(path)),
        DatabaseOwner(checkpoint_owner_path(path)),
    ):
        descriptor, name = mkstemp(
            prefix=f".{path.name}-", suffix=".db", dir=path.parent
        )
        os.close(descriptor)
        replacement = Path(name)
        try:
            fresh = Store(replacement)
            fresh.initialize(data_dir)
            count = len(fresh.records("claim"))
            fresh.dispose()
            if path.exists():
                existing = sqlite3.connect(path)
                try:
                    existing.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    with preserve_primary("Close rebuilt database checkpoint"):
                        existing.close()
            os.replace(replacement, path)
        finally:
            with preserve_primary("Remove temporary rebuild database"):
                replacement.unlink(missing_ok=True)
    return count


def rebuild_postgres(store: Store, data_dir: Path = ROOT / "data") -> int:
    """Validate first, then transactionally reseed only owned product tables."""
    with TemporaryDirectory(prefix="undercover-seed-") as directory:
        validation = Store(Path(directory) / "validated.db")
        try:
            validation.initialize(data_dir)
        finally:
            with preserve_primary("Dispose validation database"):
                validation.dispose()
    with store.owner(maintenance=True), store.transaction() as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS undercover")
        migrate(connection)
        existing = set(inspect(connection.raw).get_table_names())
        for name in ["checkpoint_writes", "checkpoint_blobs", "checkpoints"]:
            if name in existing:
                connection.raw.execute(table(name).delete())
        for definition in reversed(metadata.sorted_tables):
            connection.raw.execute(definition.delete())
        store._seed(connection, data_dir)
        store._harness_documents(connection, data_dir)
        return connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]


def main() -> int:
    """Rebuild the configured database and report the restored queue size."""
    try:
        path = database_target()
        if isinstance(path, str):
            store = Store(path)
            try:
                count = rebuild_postgres(store)
            finally:
                with preserve_primary("Dispose rebuilt database"):
                    store.dispose()
        else:
            count = rebuild(path)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        RuntimeError,
        SQLAlchemyError,
    ) as error:
        print(f"Database rebuild failed: {error}", file=sys.stderr)
        return 1
    label = "Supabase / undercover" if isinstance(path, str) else str(path)
    print(f"Rebuilt database: {label}")
    print(f"Queued {count} claims. Previous decisions, payments and activity cleared.")
    return 0


if __name__ == "__main__":
    load_environment()
    raise SystemExit(main())
