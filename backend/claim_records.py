"""Shared transaction primitives for claim lifecycle services."""

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from backend.errors import ClaimError
from backend.models import EventInput


def now() -> str:
    return datetime.now(UTC).isoformat()


def claim_row(connection: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if row is None:
        raise ClaimError(404, "Claim not found.")
    return row


def append_event(connection: sqlite3.Connection, claim_id: str, event: EventInput):
    connection.execute(
        "INSERT INTO events (claim_id, thread_id, action, status, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (claim_id, event.thread_id, event.action, event.status, event.detail, now()),
    )


def start_claim(
    connection: sqlite3.Connection, claim_id: str, thread_id: str = ""
) -> str:
    thread_id = thread_id or str(uuid4())
    connection.execute(
        "UPDATE claims SET state='in_progress', active=1, thread_id=?, "
        "run_id=NULL WHERE id=?",
        (thread_id, claim_id),
    )
    append_event(
        connection,
        claim_id,
        EventInput(
            thread_id=thread_id,
            action="Run agent",
            status="started",
            detail="Reading the claim and policy evidence.",
        ),
    )
    return thread_id
