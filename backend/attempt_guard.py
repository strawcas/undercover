"""Fence writes from expired or superseded claim helpers."""

import json

from backend.claim_records import now
from backend.database import Connection, Row
from backend.errors import ClaimError


def require_attempt(
    connection: Connection, row: Row, thread: str, execution_id: str | None = None
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
        and (json.loads(run["body"])["status"] == "running" or bool(row["decision"]))
    )
    if entry.get("execution_id") and execution_id is not None:
        valid = (
            valid
            and entry["execution_id"] == execution_id
            and bool(entry.get("owner_id"))
            and (entry.get("lease_until") or "") > now()
        )
    if not valid:
        raise ClaimError(409, "Claim attempt is no longer active.")
    return entry
