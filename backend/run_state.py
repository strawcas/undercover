"""Typed transactional views of stored queue runs."""

import json
import sqlite3

from backend.claim_records import claim_row
from backend.errors import ClaimError
from backend.harness_models import (
    Attempt,
    ClaimResult,
    CompletionCheck,
    Counts,
    Handoff,
    Run,
    StoredRun,
    TodoStatus,
)

MAX_FAILED_ATTEMPTS = 2


def read_run(connection: sqlite3.Connection, run_id: str) -> StoredRun:
    row = connection.execute(
        "SELECT body FROM agent_runs WHERE id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise ClaimError(404, "Run not found.")
    return StoredRun.model_validate_json(row["body"])


def save_run(connection: sqlite3.Connection, data: StoredRun) -> None:
    connection.execute(
        "INSERT INTO agent_runs VALUES (?, ?) "
        "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
        (data.run_id, data.model_dump_json()),
    )


def require_running(data: StoredRun) -> None:
    if data.status != "running":
        raise ClaimError(409, "Agent run is already finished.")


def eligible(row: sqlite3.Row) -> bool:
    return (
        row["state"] == "waiting"
        and not row["active"]
        and not row["decision"]
        and row["failed_attempts"] < MAX_FAILED_ATTEMPTS
    )


def progress(row: sqlite3.Row) -> tuple[TodoStatus, Handoff | None]:
    decision = json.loads(row["decision"]) if row["decision"] else {}
    conditions: tuple[tuple[bool, TodoStatus, Handoff | None], ...] = (
        (bool(row["active"]), "in_progress", None),
        (row["state"] == "handler_review", "left_for_handler", "technical_failure"),
        (not decision, "to_do", None),
        (row["state"] == "awaiting_sign_off", "left_for_handler", "sign_off"),
        (decision.get("outcome") == "refer", "left_for_handler", "referral"),
        (
            decision.get("outcome") == "approve" and not row["payment"],
            "left_for_handler",
            "unpaid_approval",
        ),
    )
    for match, status, reason in conditions:
        if match:
            return status, reason
    return "done", None


def sync_todos(connection: sqlite3.Connection, data: StoredRun) -> None:
    for item in data.todos:
        item.status, item.handoff_reason = progress(
            claim_row(connection, item.claim_id)
        )


def attempts(connection: sqlite3.Connection, run_id: str) -> list[Attempt]:
    return [
        Attempt.model_validate_json(row["body"])
        for row in connection.execute(
            "SELECT body FROM agent_attempts WHERE run_id=? ORDER BY rowid", (run_id,)
        )
    ]


def claim_result(row: sqlite3.Row) -> ClaimResult:
    decision = json.loads(row["decision"]) if row["decision"] else {}
    payment = "unavailable"
    if decision.get("outcome") == "approve":
        payment = "blocked" if row["state"] == "awaiting_sign_off" else "eligible"
    if row["payment"]:
        payment = "paid"
    return ClaimResult(
        claim_status=row["state"],
        failed_attempts=row["failed_attempts"],
        outcome=decision.get("outcome"),
        payout=decision.get("payout"),
        payment_status=payment,
    )


def counts(connection: sqlite3.Connection, data: StoredRun) -> Counts:
    entries = attempts(connection, data.run_id)
    result = Counts(
        worked=len(data.admitted_claim_ids),
        assessment_attempts=len(entries),
        failed_assessments=sum(a.status == "failed" for a in entries),
    )
    for claim_id in data.admitted_claim_ids:
        row = claim_row(connection, claim_id)
        outcome = claim_result(row)
        result.approved += outcome.outcome == "approve"
        result.declined += outcome.outcome == "decline"
        result.referred += outcome.outcome == "refer"
        result.simulated_payments += bool(row["payment"])
        result.awaiting_sign_off += row["state"] == "awaiting_sign_off"
        result.handler_review += row["state"] == "handler_review"
        result.unpaid_approvals += outcome.payment_status == "eligible"
    result.waiting_remaining = connection.execute(
        "SELECT COUNT(*) FROM claims WHERE state='waiting'"
    ).fetchone()[0]
    return result


def view(connection: sqlite3.Connection, data: StoredRun) -> Run:
    entries = attempts(connection, data.run_id)
    totals = data.counts
    if data.status == "running":
        sync_todos(connection, data)
        totals = counts(connection, data)
        for entry in entries:
            if entry.status == "active":
                result = claim_result(claim_row(connection, entry.claim_id))
                entry.claim_status = result.claim_status
                entry.failed_attempts = result.failed_attempts
                entry.outcome = result.outcome
                entry.payout = result.payout
                entry.payment_status = result.payment_status
    return Run.model_validate(
        data.model_dump()
        | {
            "attempts": entries,
            "counts": totals,
            "capacity_remaining": data.claim_limit - len(data.admitted_claim_ids),
        }
    )


def completion(connection: sqlite3.Connection, data: StoredRun) -> CompletionCheck:
    if data.status != "running":
        if data.final_check is None:
            raise RuntimeError("Finished run has no completion check.")
        return data.final_check
    remaining = data.claim_limit - len(data.admitted_claim_ids)
    missing, retryable, active, unresolved = [], [], [], []
    admitted = set(data.admitted_claim_ids)
    for claim_id in data.snapshot_claim_ids:
        row = claim_row(connection, claim_id)
        if eligible(row):
            if claim_id in admitted:
                retryable.append(claim_id)
            elif remaining:
                missing.append(claim_id)
        if row["active"] and claim_id in admitted:
            active.append(claim_id)
    for item in data.todos:
        actual = progress(claim_row(connection, item.claim_id))
        if item.claim_id in admitted and (item.status, item.handoff_reason) != actual:
            unresolved.append(item.id)
    return CompletionCheck(
        run_id=data.run_id,
        complete=not (missing or retryable or active or unresolved),
        capacity_remaining=remaining,
        missing_claim_ids=missing,
        retryable_claim_ids=retryable,
        active_claim_ids=active,
        unresolved_todo_ids=unresolved,
        counts=counts(connection, data),
    )
