"""Claim lifecycle and payment rules enforced in SQLite transactions."""

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from backend.models import DecisionInput, EventInput, cents, dollars
from backend.storage import Store


class ClaimError(Exception):
    """An invalid operation on a claim."""

    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def now() -> str:
    return datetime.now(UTC).isoformat()


def claim_row(connection: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM claims WHERE id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        raise ClaimError(404, "Claim not found.")
    return row


def append_event(connection: sqlite3.Connection, claim_id: str, event: EventInput):
    connection.execute(
        "INSERT INTO events (claim_id, thread_id, action, status, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (claim_id, event.thread_id, event.action, event.status, event.detail, now()),
    )


class Claims:
    """Use the store for claim context and atomic financial changes."""

    def __init__(self, store: Store):
        self.store = store

    def detail(self, claim_id: str) -> dict[str, Any]:
        with self.store.connect() as connection:
            row = claim_row(connection, claim_id)
        claim = self.store.record("claim", claim_id)
        policy = self.store.record("policy", claim["policy_id"])
        plans = self.store.record("settings", "plans")
        return {
            **claim,
            "status": row["state"],
            "thread_id": row["thread_id"],
            "run_active": bool(row["active"]),
            "policy": policy,
            "customer": self.store.record("customer", claim["customer_id"]),
            "plan": next(p for p in plans["plans"] if p["plan"] == policy["plan"]),
            "add_ons": [
                a for a in plans["add_ons"] if a["add_on"] in policy["add_ons"]
            ],
            "decision": json.loads(row["decision"]) if row["decision"] else None,
            "sign_off": json.loads(row["sign_off"]) if row["sign_off"] else None,
            "payment": json.loads(row["payment"]) if row["payment"] else None,
        }

    def queue(self) -> list[dict[str, Any]]:
        rows = []
        for record in self.store.records("claim"):
            detail = self.detail(record["claim_id"])
            decision = detail["decision"]
            row = {
                key: detail[key]
                for key in (
                    "claim_id",
                    "claim_type",
                    "summary",
                    "amount_claimed",
                    "status",
                )
            }
            rows.append(
                row
                | {
                    "customer_name": detail["customer"]["name"],
                    "outcome": decision["outcome"] if decision else None,
                    "payment_status": self._payment_status(detail),
                    "run_active": detail["run_active"],
                }
            )
        return rows

    @staticmethod
    def _payment_status(detail: dict[str, Any]) -> str:
        if detail["payment"]:
            return "paid"
        if detail["status"] == "awaiting_sign_off":
            return "blocked"
        if detail["decision"] and detail["decision"]["outcome"] == "approve":
            return "eligible"
        return "unavailable"

    def start(self, claim_id: str) -> str:
        thread_id = str(uuid4())
        with self.store.transaction() as connection:
            row = claim_row(connection, claim_id)
            if row["active"] or row["decision"]:
                raise ClaimError(409, "This claim is already running or decided.")
            connection.execute(
                "UPDATE claims SET state='in_progress', active=1, thread_id=? "
                "WHERE id=?",
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

    def decide(self, claim_id: str, decision: DecisionInput) -> dict[str, Any]:
        company = self.store.record("settings", "company")
        payout = cents(decision.payout)
        over_limit = payout > cents(Decimal(company["payout_approval_limit"]))
        result = decision.model_dump(mode="json") | {"created_at": now()}
        state = "awaiting_sign_off" if over_limit else "decided"
        with self.store.transaction() as connection:
            row = claim_row(connection, claim_id)
            if not row["active"] or row["thread_id"] != decision.thread_id:
                raise ClaimError(409, "The decision must belong to the active run.")
            if row["decision"]:
                raise ClaimError(409, "The claim already has a decision.")
            connection.execute(
                "UPDATE claims SET decision=?, payout_cents=?, state=? WHERE id=?",
                (json.dumps(result), payout, state, claim_id),
            )
            append_event(
                connection,
                claim_id,
                EventInput(
                    thread_id=decision.thread_id,
                    action="Record decision",
                    status="completed",
                    detail=(
                        f"{decision.outcome.title()}: ${dollars(payout)}. "
                        f"{decision.explanation}"
                    ),
                ),
            )
        return result

    def sign_off(self, claim_id: str, handler_id: str) -> dict[str, Any]:
        handlers = self.store.record("settings", "handlers")
        handler = next((h for h in handlers if h["handler_id"] == handler_id), None)
        if handler is None:
            raise ClaimError(404, "Handler not found.")
        if not handler["can_approve_over_limit"]:
            raise ClaimError(409, "This handler cannot sign off this payout.")
        with self.store.transaction() as connection:
            row = claim_row(connection, claim_id)
            if row["state"] != "awaiting_sign_off":
                raise ClaimError(409, "This claim is not waiting for sign-off.")
            result = {
                "handler_id": handler_id,
                "handler_name": handler["name"],
                "amount": dollars(row["payout_cents"]),
                "created_at": now(),
            }
            connection.execute(
                "UPDATE claims SET sign_off=?, state='decided' WHERE id=?",
                (json.dumps(result), claim_id),
            )
            append_event(
                connection,
                claim_id,
                EventInput(
                    thread_id=row["thread_id"],
                    action="Handler sign-off",
                    status="completed",
                    detail=f"{handler['name']} signed off ${result['amount']}.",
                ),
            )
        return result

    def pay(self, claim_id: str, amount: Decimal) -> dict[str, Any]:
        limit = cents(
            Decimal(self.store.record("settings", "company")["payout_approval_limit"])
        )
        with self.store.transaction() as connection:
            row = claim_row(connection, claim_id)
            self._check_payment(row, amount, limit)
            result = {
                "amount": dollars(cents(amount)),
                "created_at": now(),
                "simulated": True,
            }
            connection.execute(
                "UPDATE claims SET payment=? WHERE id=?", (json.dumps(result), claim_id)
            )
            append_event(
                connection,
                claim_id,
                EventInput(
                    thread_id=row["thread_id"],
                    action="Simulated payment",
                    status="completed",
                    detail=(
                        f"Recorded ${result['amount']}. No real money was transferred."
                    ),
                ),
            )
        return result

    @staticmethod
    def _check_payment(row: sqlite3.Row, amount: Decimal, limit: int) -> None:
        decision = json.loads(row["decision"]) if row["decision"] else {}
        if decision.get("outcome") != "approve":
            raise ClaimError(409, "Only an approved claim can be paid.")
        if row["payment"]:
            raise ClaimError(409, "Payment has already been recorded.")
        if cents(amount) != row["payout_cents"]:
            raise ClaimError(409, "Payment must equal the approved payout.")
        sign_off = json.loads(row["sign_off"]) if row["sign_off"] else {}
        if row["payout_cents"] > limit and sign_off.get("amount") != dollars(
            row["payout_cents"]
        ):
            raise ClaimError(409, "A handler must sign off this payout before payment.")

    def events(self, claim_id: str) -> list[dict[str, Any]]:
        with self.store.connect() as connection:
            claim_row(connection, claim_id)
            rows = connection.execute(
                "SELECT * FROM events WHERE claim_id=? ORDER BY id", (claim_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def event(self, claim_id: str, event: EventInput) -> None:
        with self.store.transaction() as connection:
            row = claim_row(connection, claim_id)
            if row["thread_id"] != event.thread_id or not row["active"]:
                raise ClaimError(409, "The event must belong to the active run.")
            append_event(connection, claim_id, event)
