"""Transactional views of stored queue runs."""

import json
import sqlite3
from typing import Any

from backend.claim_records import claim_row
from backend.errors import ClaimError
from backend.harness_models import Counts

Record = dict[str, Any]
MAX_FAILED_ATTEMPTS = 2


def read_run(connection: sqlite3.Connection, run_id: str) -> Record:
    row = connection.execute(
        "SELECT body FROM agent_runs WHERE id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise ClaimError(404, "Run not found.")
    return json.loads(row["body"])


def save_run(connection: sqlite3.Connection, data: Record) -> None:
    connection.execute(
        "INSERT INTO agent_runs VALUES (?, ?) "
        "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
        (data["run_id"], json.dumps(data)),
    )


def require_running(data: Record) -> None:
    if data["status"] != "running":
        raise ClaimError(409, "Agent run is already finished.")


def eligible(row: sqlite3.Row) -> bool:
    return (
        row["state"] == "waiting"
        and not row["active"]
        and not row["decision"]
        and row["failed_attempts"] < MAX_FAILED_ATTEMPTS
    )


def progress(row: sqlite3.Row) -> tuple[str, str | None]:
    decision = json.loads(row["decision"]) if row["decision"] else {}
    conditions = (
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
    return next(
        ((status, reason) for match, status, reason in conditions if match),
        ("done", None),
    )


def sync_todos(connection: sqlite3.Connection, data: Record) -> None:
    for item in data["todos"]:
        row = claim_row(connection, item["claim_id"])
        item["status"], item["handoff_reason"] = progress(row)


def attempts(connection: sqlite3.Connection, run_id: str) -> list[Record]:
    return [
        json.loads(row["body"])
        for row in connection.execute(
            "SELECT body FROM agent_attempts WHERE run_id=? ORDER BY rowid", (run_id,)
        )
    ]


def claim_result(row: sqlite3.Row) -> Record:
    decision = json.loads(row["decision"]) if row["decision"] else {}
    payment = "unavailable"
    if decision.get("outcome") == "approve":
        payment = "blocked" if row["state"] == "awaiting_sign_off" else "eligible"
    if row["payment"]:
        payment = "paid"
    return dict(
        claim_status=row["state"],
        failed_attempts=row["failed_attempts"],
        outcome=decision.get("outcome"),
        payout=decision.get("payout"),
        payment_status=payment,
    )


def counts(connection: sqlite3.Connection, data: Record) -> Record:
    result = Counts().model_dump()
    result["worked"] = len(data["admitted_claim_ids"])
    entries = attempts(connection, data["run_id"])
    result["assessment_attempts"] = len(entries)
    result["failed_assessments"] = sum(a["status"] == "failed" for a in entries)
    for claim_id in data["admitted_claim_ids"]:
        row = claim_row(connection, claim_id)
        outcome = claim_result(row)
        for key, value in (
            ("approved", "approve"),
            ("declined", "decline"),
            ("referred", "refer"),
        ):
            result[key] += outcome["outcome"] == value
        result["simulated_payments"] += bool(row["payment"])
        result["awaiting_sign_off"] += row["state"] == "awaiting_sign_off"
        result["handler_review"] += row["state"] == "handler_review"
        result["unpaid_approvals"] += outcome["payment_status"] == "eligible"
    result["waiting_remaining"] = connection.execute(
        "SELECT COUNT(*) FROM claims WHERE state='waiting'"
    ).fetchone()[0]
    return result


def view(connection: sqlite3.Connection, data: Record) -> Record:
    result = dict(data)
    result["attempts"] = attempts(connection, data["run_id"])
    result["capacity_remaining"] = data["claim_limit"] - len(data["admitted_claim_ids"])
    if data["status"] == "running":
        sync_todos(connection, result)
        result["counts"] = counts(connection, data)
        for entry in result["attempts"]:
            if entry["status"] == "active":
                entry.update(claim_result(claim_row(connection, entry["claim_id"])))
    return result


def completion(connection: sqlite3.Connection, data: Record) -> Record:
    if data["status"] != "running":
        return data["final_check"]
    remaining = data["claim_limit"] - len(data["admitted_claim_ids"])
    missing, retryable, active, unresolved = [], [], [], []
    admitted = set(data["admitted_claim_ids"])
    for claim_id in data["snapshot_claim_ids"]:
        row = claim_row(connection, claim_id)
        if eligible(row):
            if claim_id in admitted:
                retryable.append(claim_id)
            elif remaining:
                missing.append(claim_id)
        if row["active"] and claim_id in admitted:
            active.append(claim_id)
    for item in data["todos"]:
        actual = progress(claim_row(connection, item["claim_id"]))
        if (
            item["claim_id"] in admitted
            and (item["status"], item["handoff_reason"]) != actual
        ):
            unresolved.append(item["id"])
    return dict(
        run_id=data["run_id"],
        complete=not (missing or retryable or active or unresolved),
        capacity_remaining=remaining,
        missing_claim_ids=missing,
        retryable_claim_ids=retryable,
        active_claim_ids=active,
        unresolved_todo_ids=unresolved,
        counts=counts(connection, data),
    )
