"""Fence writes from expired or superseded claim helpers."""

import json
import sqlite3

from backend.claim_records import now
from backend.errors import ClaimError


def require_attempt(
    connection: sqlite3.Connection, row: sqlite3.Row, thread: str
) -> dict:
    stored = connection.execute(
        "SELECT body FROM agent_attempts WHERE thread_id=?", (thread,)
    ).fetchone()
    if stored is None:
        raise ClaimError(409, "Claim attempt is no longer active.")
    entry = json.loads(stored["body"])
    run = connection.execute(
        "SELECT body FROM agent_runs WHERE id=?", (entry["run_id"],)
    ).fetchone()
    valid = (
        row["id"] == entry["claim_id"]
        and row["thread_id"] == thread
        and row["active"]
        and entry["status"] == "active"
        and entry["deadline_at"] > now()
        and run is not None
        and json.loads(run["body"])["status"] == "running"
    )
    if not valid:
        raise ClaimError(409, "Claim attempt is no longer active.")
    return entry
