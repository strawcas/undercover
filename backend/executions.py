"""Execution ownership and durable suspension of existing assessments."""

import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from backend.claim_records import append_event, claim_row, now
from backend.database import Connection
from backend.errors import ClaimError
from backend.harness_models import Attempt, RequiredInput
from backend.models import EventInput
from backend.run_state import claim_result
from backend.storage import Store

LEASE_SECONDS = 5


class ExecutionInput(RequiredInput):
    thread_id: UUID
    owner_id: UUID


class ReleaseInput(ExecutionInput):
    mode: Literal["release", "pause", "suspend"]


def read_attempt(connection: Connection, thread_id: str) -> Attempt:
    row = connection.execute(
        "SELECT body FROM agent_attempts WHERE thread_id=?", (thread_id,)
    ).fetchone()
    if row is None:
        raise ClaimError(404, "Claim attempt not found.")
    return Attempt.model_validate_json(row["body"])


def save_attempt(connection: Connection, entry: Attempt) -> None:
    connection.execute(
        "UPDATE agent_attempts SET body=? WHERE thread_id=?",
        (entry.model_dump_json(), entry.thread_id),
    )


def remaining(entry: Attempt) -> float:
    if entry.owner_id and entry.lease_until:
        end = min(datetime.now(UTC), datetime.fromisoformat(entry.lease_until))
        return max(0, (datetime.fromisoformat(entry.deadline_at) - end).total_seconds())
    return entry.remaining_seconds


def suspend(connection: Connection, entry: Attempt, detail: str) -> Attempt:
    if entry.status != "active":
        return entry
    row = claim_row(connection, entry.claim_id)
    entry.remaining_seconds = remaining(entry)
    entry.status = (
        "paused"
        if row["state"] == "awaiting_sign_off" and not row["sign_off"]
        else "suspended"
    )
    entry.owner_id, entry.lease_until = None, None
    state = row["state"] if row["decision"] else "in_progress"
    connection.execute(
        "UPDATE claims SET active=0,state=? WHERE id=?", (state, entry.claim_id)
    )
    entry = entry.model_copy(
        update=claim_result(claim_row(connection, entry.claim_id)).model_dump()
    )
    save_attempt(connection, entry)
    append_event(
        connection,
        entry.claim_id,
        EventInput(
            thread_id=entry.thread_id,
            action="Suspend assessment",
            status="blocked",
            detail=detail,
        ),
    )
    return entry


class Executions:
    def __init__(self, store: Store):
        self.store = store

    def acquire(self, claim_id: str, request: ExecutionInput) -> Attempt:
        with self.store.transaction() as connection:
            row = claim_row(connection, claim_id)
            entry = read_attempt(connection, str(request.thread_id))
            if entry.claim_id != claim_id or row["thread_id"] != entry.thread_id:
                raise ClaimError(
                    409, "The execution must belong to the saved assessment."
                )
            if entry.status == "failed" and not row["decision"]:
                raise ClaimError(
                    409, "A failed assessment needs a fresh eligible attempt."
                )
            if entry.owner_id and entry.lease_until and entry.lease_until > now():
                if entry.owner_id == str(request.owner_id):
                    return entry
                raise ClaimError(409, "Assessment is still owned by another execution.")
            if entry.status == "completed" and (
                row["payment"]
                or json.loads(row["decision"] or "{}").get("outcome") != "approve"
            ):
                return entry
            if row["state"] == "awaiting_sign_off" and not row["sign_off"]:
                return entry.model_copy(update={"status": "paused"})
            other = connection.execute(
                "SELECT id FROM claims WHERE active=1 AND id<>?", (claim_id,)
            ).fetchone()
            if other:
                raise ClaimError(409, "Another claim is already running.")
            allowance = remaining(entry)
            started = datetime.now(UTC)
            entry.status, entry.owner_id = "active", str(request.owner_id)
            entry.execution_id = str(uuid4())
            entry.remaining_seconds = allowance
            entry.deadline_at = (started + timedelta(seconds=allowance)).isoformat()
            entry.lease_until = (started + timedelta(seconds=LEASE_SECONDS)).isoformat()
            entry.finished_at, entry.error = None, None
            connection.execute(
                "UPDATE claims SET active=1,state=CASE WHEN payment IS NOT NULL "
                "THEN 'decided' ELSE 'in_progress' END WHERE id=?",
                (claim_id,),
            )
            entry = entry.model_copy(
                update=claim_result(claim_row(connection, claim_id)).model_dump()
            )
            save_attempt(connection, entry)
            return entry

    def heartbeat(
        self, claim_id: str, execution_id: str, request: ExecutionInput
    ) -> Attempt:
        with self.store.transaction() as connection:
            entry = self._owned(connection, claim_id, execution_id, request)
            entry.lease_until = (
                datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
            ).isoformat()
            save_attempt(connection, entry)
            return entry

    @staticmethod
    def _owned(
        connection: Connection,
        claim_id: str,
        execution_id: str,
        request: ExecutionInput,
    ) -> Attempt:
        entry = read_attempt(connection, str(request.thread_id))
        if (
            entry.claim_id != claim_id
            or entry.execution_id != execution_id
            or entry.owner_id != str(request.owner_id)
            or not entry.lease_until
            or entry.lease_until <= now()
        ):
            raise ClaimError(409, "Claim execution is no longer active.")
        return entry

    def release(
        self, claim_id: str, execution_id: str, request: ReleaseInput
    ) -> Attempt:
        with self.store.transaction() as connection:
            entry = read_attempt(connection, str(request.thread_id))
            if entry.claim_id != claim_id or entry.execution_id != execution_id:
                raise ClaimError(409, "Claim execution is no longer active.")
            if entry.owner_id is None:
                return entry
            if entry.owner_id != str(request.owner_id):
                raise ClaimError(409, "Claim execution is no longer active.")
            if request.mode == "suspend":
                return suspend(
                    connection,
                    entry,
                    "Execution stopped; the next explicit run resumes saved work.",
                )
            entry.remaining_seconds = remaining(entry)
            entry.owner_id, entry.lease_until = None, None
            row = claim_row(connection, claim_id)
            if request.mode == "pause":
                if row["state"] != "awaiting_sign_off" and not row["sign_off"]:
                    raise ClaimError(409, "Only a held approval can wait for sign-off.")
                entry.status = "suspended" if row["sign_off"] else "paused"
                connection.execute("UPDATE claims SET active=0 WHERE id=?", (claim_id,))
                append_event(
                    connection,
                    claim_id,
                    EventInput(
                        thread_id=entry.thread_id,
                        action="Pause assessment",
                        status="blocked",
                        detail="Saved assessment is waiting for handler sign-off.",
                    ),
                )
            entry = entry.model_copy(
                update=claim_result(claim_row(connection, claim_id)).model_dump()
            )
            save_attempt(connection, entry)
            return entry

    def pending(self) -> list[Attempt]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT a.body FROM agent_attempts a JOIN claims c "
                "ON c.thread_id=a.thread_id ORDER BY c.queue_position"
            ).fetchall()
            result = []
            for stored in rows:
                entry = Attempt.model_validate_json(stored["body"])
                row = claim_row(connection, entry.claim_id)
                unpaid = (
                    json.loads(row["decision"] or "{}").get("outcome") == "approve"
                    and not row["payment"]
                    and row["state"] != "awaiting_sign_off"
                )
                expired = (
                    entry.status == "active"
                    and entry.owner_id
                    and entry.lease_until
                    and entry.lease_until <= now()
                )
                available = not (
                    entry.owner_id and entry.lease_until and entry.lease_until > now()
                )
                unsigned = row["state"] == "awaiting_sign_off" and not row["sign_off"]
                if (
                    available
                    and not unsigned
                    and (entry.status == "suspended" or unpaid or expired)
                ):
                    result.append(entry)
            return result

    def recover_expired(self) -> None:
        with self.store.transaction() as connection:
            for row in connection.execute("SELECT body FROM agent_attempts"):
                entry = Attempt.model_validate_json(row["body"])
                if (
                    entry.status == "active"
                    and entry.owner_id
                    and entry.lease_until
                    and entry.lease_until <= now()
                ):
                    suspend(
                        connection,
                        entry,
                        "The previous execution stopped; saved work is resumable.",
                    )
