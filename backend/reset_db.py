"""Explicitly replace the local database with a validated fresh source queue."""

import os
import sqlite3
import sys
from pathlib import Path
from tempfile import mkstemp

from backend.config import ROOT, database_path
from backend.diagnostics import preserve_primary
from backend.ownership import DatabaseOwner
from backend.storage import Store


def rebuild(path: Path, data_dir: Path = ROOT / "data") -> int:
    """Rebuild only the selected database, preserving it if source validation fails."""
    path = path.resolve()
    with DatabaseOwner(path):
        descriptor, name = mkstemp(
            prefix=f".{path.name}-", suffix=".db", dir=path.parent
        )
        os.close(descriptor)
        replacement = Path(name)
        try:
            fresh = Store(replacement)
            fresh.initialize(data_dir)
            count = len(fresh.records("claim"))
            os.replace(replacement, path)
        finally:
            with preserve_primary("Remove temporary rebuild database"):
                replacement.unlink(missing_ok=True)
    return count


def main() -> int:
    """Rebuild the configured database and report the restored queue size."""
    try:
        path = database_path()
        count = rebuild(path)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        RuntimeError,
    ) as error:
        print(f"Database rebuild failed: {error}", file=sys.stderr)
        return 1
    print(f"Rebuilt database: {path}")
    print(f"Queued {count} claims. Previous decisions, payments and activity cleared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
