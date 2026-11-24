"""Timestamp generation shared by persisted records."""

from datetime import UTC, datetime, timedelta

STALE_AFTER = timedelta(minutes=5)


def now() -> str:
    """Return the current UTC time with subsecond precision."""
    return datetime.now(UTC).isoformat()


def has_recent_heartbeat(heartbeat: str | None, at: str) -> bool:
    """Treat a job as stale after five minutes of silence."""
    return bool(
        heartbeat
        and datetime.fromisoformat(at) - datetime.fromisoformat(heartbeat) < STALE_AFTER
    )


def has_live_lease(started: str, heartbeat: str | None, at: str) -> bool:
    """Use the work start until a newer heartbeat is recorded."""
    return has_recent_heartbeat(max(started, heartbeat or started), at)
