"""Timestamp generation shared by persisted records."""

from datetime import UTC, datetime


def now() -> str:
    """Return the current UTC time with subsecond precision."""
    return datetime.now(UTC).isoformat()
