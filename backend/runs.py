"""Scoped admission, retries and completion of local agent runs."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from backend.claim_records import append_event, claim_row, now, start_claim
from backend.errors import ClaimError
from backend.harness_models import AttemptInput, RunFinishInput, RunInput, TodosInput
from backend.models import EventInput
from backend.run_state import (
    MAX_FAILED_ATTEMPTS,
    Record,
    attempts,
    claim_result,
    completion,
    counts,
    eligible,
    progress,
    read_run,
    require_running,
    save_run,
    sync_todos,
    view,
)
from backend.storage import Store

ATTEMPT_TIMEOUT_SECONDS = 600


class Runs:
    """Keep the run's financial and workload boundaries in transactions."""

    def __init__(self, store: Store):
        self.store = store

    def get(self, run_id: str) -> Record:
        with self.store.connect() as connection:
            return view(connection, read_run(connection, run_id))

    def current(self) -> Record | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT body FROM agent_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            return view(connection, json.loads(row["body"])) if row else None

    def create(self, request: RunInput) -> tuple[Record, bool]:
        run_id = str(request.run_id)
        scope = request.scope.model_dump()
        with self.store.transaction() as connection:
            prior = connection.execute(
                "SELECT body FROM agent_runs WHERE id=?", (run_id,)
            ).fetchone()
            if prior:
                data = json.loads(prior["body"])
                if data["scope"] != scope:
                    raise ClaimError(
                        409, "Run ID is already used with a different scope."
                    )
                return view(connection, data), False
            for row in connection.execute("SELECT body FROM agent_runs"):
                if json.loads(row["body"])["status"] == "running":
                    raise ClaimError(409, "Another agent run is active.")
            snapshot = self._snapshot(connection, scope)
            limit = min(scope.get("max_claims", len(snapshot)), len(snapshot))
            data = dict(
                run_id=run_id,
                scope=scope,
                status="running",
                claim_limit=limit,
                snapshot_claim_ids=snapshot,
                admitted_claim_ids=[],
                todos=[],
                started_at=now(),
                finished_at=None,
                summary=None,
            )
            save_run(connection, data)
            return view(connection, data), True

    @staticmethod
    def _snapshot(connection: sqlite3.Connection, scope: Record) -> list[str]:
        if scope["mode"] == "claim":
            row = claim_row(connection, scope["claim_id"])
            if not eligible(row):
                raise ClaimError(409, "Claim is not eligible for assessment.")
            return [row["id"]]
        return [
            row["id"]
            for row in connection.execute(
                "SELECT * FROM claims ORDER BY queue_position"
            )
            if eligible(row)
        ]

    def todos(self, run_id: str, request: TodosInput) -> Record:
        with self.store.transaction() as connection:
            data = read_run(connection, run_id)
            require_running(data)
            if len(request.items) > data["claim_limit"]:
                raise ClaimError(409, "To-do list exceeds the run claim limit.")
            items = [
                item.model_dump() | {"handoff_reason": None} for item in request.items
            ]
            self._validate_todos(connection, data, items)
            data["todos"] = items
            sync_todos(connection, data)
            save_run(connection, data)
            return dict(run_id=run_id, items=data["todos"], updated_at=now())

    @staticmethod
    def _validate_todos(
        connection: sqlite3.Connection, data: Record, items: list[Record]
    ):
        by_claim = {item["claim_id"]: item for item in items}
        for old in data["todos"]:
            if old["claim_id"] in data["admitted_claim_ids"]:
                new = by_claim.get(old["claim_id"])
                if new is None or new["id"] != old["id"]:
                    raise ClaimError(
                        409, "To-do list does not match stored claim progress."
                    )
        for item in items:
            row = claim_row(connection, item["claim_id"])
            if item["claim_id"] not in data["snapshot_claim_ids"]:
                raise ClaimError(409, "Claim is outside this run's scope.")
            if item["status"] != progress(row)[0]:
                raise ClaimError(
                    409, "To-do list does not match stored claim progress."
                )

    def start(self, run_id: str, request: AttemptInput) -> tuple[Record, bool]:
        thread = str(request.thread_id)
        with self.store.transaction() as connection:
            data = read_run(connection, run_id)
            row = claim_row(connection, request.claim_id)
            prior = connection.execute(
                "SELECT * FROM agent_attempts WHERE thread_id=?", (thread,)
            ).fetchone()
            if prior:
                if prior["run_id"] != run_id or prior["claim_id"] != request.claim_id:
                    raise ClaimError(
                        409, "Attempt request conflicts with the recorded attempt."
                    )
                return json.loads(prior["body"]), False
            require_running(data)
            self._admit(connection, data, row)
            start_claim(connection, request.claim_id, thread)
            connection.execute(
                "UPDATE claims SET run_id=? WHERE id=?", (run_id, row["id"])
            )
            started = datetime.now(UTC)
            number = (
                connection.execute(
                    "SELECT COUNT(*) FROM agent_attempts WHERE claim_id=?", (row["id"],)
                ).fetchone()[0]
                + 1
            )
            entry = dict(
                run_id=run_id,
                claim_id=row["id"],
                thread_id=thread,
                attempt_number=number,
                status="active",
                started_at=started.isoformat(),
                deadline_at=(
                    started + timedelta(seconds=ATTEMPT_TIMEOUT_SECONDS)
                ).isoformat(),
                finished_at=None,
                error=None,
                **claim_result(claim_row(connection, row["id"])),
            )
            connection.execute(
                "INSERT INTO agent_attempts VALUES (?, ?, ?, ?, NULL)",
                (thread, run_id, row["id"], json.dumps(entry)),
            )
            sync_todos(connection, data)
            save_run(connection, data)
            return entry, True

    @staticmethod
    def _admit(connection: sqlite3.Connection, data: Record, row: sqlite3.Row):
        if row["id"] not in data["snapshot_claim_ids"]:
            raise ClaimError(409, "Claim is outside this run's scope.")
        if not eligible(row):
            raise ClaimError(409, "Claim is not eligible for assessment.")
        if connection.execute("SELECT 1 FROM claims WHERE active=1").fetchone():
            raise ClaimError(409, "Another claim is already running.")
        if row["id"] not in data["admitted_claim_ids"]:
            if len(data["admitted_claim_ids"]) >= data["claim_limit"]:
                raise ClaimError(409, "Run claim limit reached.")
            data["admitted_claim_ids"].append(row["id"])
        if not any(item["claim_id"] == row["id"] for item in data["todos"]):
            raise ClaimError(409, "To-do list does not match stored claim progress.")

    def finish_attempt(self, run_id: str, thread: str, error: str) -> Record:
        with self.store.transaction() as connection:
            data = read_run(connection, run_id)
            entry = self._finish_attempt(connection, data, thread, error)
            if data["status"] == "running":
                sync_todos(connection, data)
                save_run(connection, data)
            return entry

    @staticmethod
    def _finish_attempt(
        connection: sqlite3.Connection, data: Record, thread: str, error: str
    ) -> Record:
        stored = connection.execute(
            "SELECT * FROM agent_attempts WHERE thread_id=? AND run_id=?",
            (thread, data["run_id"]),
        ).fetchone()
        if stored is None:
            raise ClaimError(404, "Claim attempt not found.")
        entry = json.loads(stored["body"])
        if entry["status"] != "active":
            if stored["finish_input"] != error:
                raise ClaimError(
                    409, "Attempt request conflicts with the recorded attempt."
                )
            return entry
        require_running(data)
        row = claim_row(connection, entry["claim_id"])
        if row["thread_id"] != thread or not row["active"]:
            raise ClaimError(409, "Claim attempt is no longer active.")
        if entry["deadline_at"] <= now() and not error:
            raise ClaimError(409, "Claim attempt is no longer active.")
        result = claim_result(row)
        failure = error or ("No decision was recorded." if not row["decision"] else "")
        if result["payment_status"] == "eligible" and not failure:
            failure = "Approved payment was not recorded."
        Runs._close_claim(connection, row, failure)
        entry.update(
            status="failed" if failure else "completed",
            error=failure or None,
            finished_at=now(),
            **claim_result(claim_row(connection, row["id"])),
        )
        connection.execute(
            "UPDATE agent_attempts SET body=?, finish_input=? WHERE thread_id=?",
            (json.dumps(entry), error, thread),
        )
        return entry

    @staticmethod
    def _close_claim(connection: sqlite3.Connection, row: sqlite3.Row, failure: str):
        failures = row["failed_attempts"] + int(not row["decision"])
        state, reason = row["state"], None
        if not row["decision"]:
            state = "handler_review" if failures >= MAX_FAILED_ATTEMPTS else "waiting"
            reason = (
                "two_failed_assessments" if failures >= MAX_FAILED_ATTEMPTS else None
            )
        elif claim_result(row)["payment_status"] == "eligible":
            reason = "payment_failed"
        connection.execute(
            "UPDATE claims SET active=0, state=?, failed_attempts=?, "
            "review_reason=? WHERE id=?",
            (state, failures, reason, row["id"]),
        )
        append_event(
            connection,
            row["id"],
            EventInput(
                thread_id=row["thread_id"],
                action="Run agent",
                status="failed" if failure else "completed",
                detail=failure or "Decision recorded. Agent run finished.",
            ),
        )

    def check(self, run_id: str) -> Record:
        with self.store.connect() as connection:
            data = read_run(connection, run_id)
            sync_todos(connection, data) if data["status"] == "running" else None
            return completion(connection, data)

    def finish(self, run_id: str, request: RunFinishInput) -> Record:
        with self.store.transaction() as connection:
            data = read_run(connection, run_id)
            payload = request.model_dump()
            if data["status"] != "running":
                if data["finish_input"] != payload:
                    raise ClaimError(
                        409, "Run finish conflicts with the recorded result."
                    )
                return view(connection, data)
            if request.status != "completed":
                for entry in attempts(connection, run_id):
                    if entry["status"] == "active":
                        self._finish_attempt(
                            connection, data, entry["thread_id"], request.detail
                        )
            sync_todos(connection, data)
            check = completion(connection, data)
            if request.status == "completed" and not check["complete"]:
                raise ClaimError(409, "Run has unfinished work.")
            totals = counts(connection, data)
            text = (
                f"Worked {totals['worked']} claims in "
                f"{totals['assessment_attempts']} attempts: "
                f"{totals['approved']} approved, {totals['declined']} declined, "
                f"{totals['referred']} referred. "
                f"{totals['handler_review']} need handler review; "
                f"{totals['awaiting_sign_off']} await sign-off; "
                f"{totals['waiting_remaining']} remain waiting."
            )
            data.update(
                status=request.status,
                finished_at=now(),
                counts=totals,
                summary=dict(text=text, detail=request.detail, counts=totals),
                final_check=check,
                finish_input=payload,
            )
            save_run(connection, data)
            return view(connection, data)

    def recover(self) -> None:
        with self.store.connect() as connection:
            ids = [
                row["id"]
                for row in connection.execute("SELECT id FROM agent_runs")
                if read_run(connection, row["id"])["status"] == "running"
            ]
        for run_id in ids:
            self.finish(
                run_id,
                RunFinishInput(
                    status="interrupted", detail="Backend restarted during this run."
                ),
            )
