"""Scoped admission, retries and completion of local agent runs."""

import json
from datetime import UTC, datetime, timedelta

from backend.claim_records import append_event, claim_row, now, start_claim
from backend.database import Connection, Row
from backend.database import attempts as attempts_table
from backend.errors import ClaimError
from backend.executions import Executions, read_attempt, suspend
from backend.harness_models import (
    Attempt,
    AttemptInput,
    LimitedScope,
    RunFinishInput,
    RunInput,
    Scope,
    StoredRun,
    Summary,
    Todo,
    TodosInput,
)
from backend.models import EventInput
from backend.run_state import (
    MAX_FAILED_ATTEMPTS,
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

    def __init__(
        self,
        store: Store,
        execution_id: str | None = None,
        run_generation: str | None = None,
    ):
        self.store = store
        self.execution_id = execution_id
        self.run_generation = run_generation

    def require_generation(self, data: StoredRun) -> None:
        if (
            data.execution_generation > 1
            and self.run_generation is not None
            and self.run_generation != str(data.execution_generation)
        ):
            raise ClaimError(409, "Queue execution has been superseded.")

    def get(self, run_id: str) -> dict:
        with self.store.connect() as connection:
            return view(connection, read_run(connection, run_id)).model_dump(
                mode="json"
            )

    def current(self) -> dict | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT body FROM agent_runs ORDER BY position DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            data = StoredRun.model_validate_json(row["body"])
            return view(connection, data).model_dump(mode="json")

    def create(self, request: RunInput) -> tuple[dict, bool]:
        Executions(self.store).recover_expired()
        self._recover_abandoned_runs()
        run_id = str(request.run_id)
        scope = request.scope
        with self.store.transaction() as connection:
            alias = connection.execute(
                "SELECT run_id,scope FROM run_requests WHERE request_id=?", (run_id,)
            ).fetchone()
            if alias:
                if json.loads(alias["scope"]) != scope.model_dump(mode="json"):
                    raise ClaimError(
                        409, "Run ID is already used with a different scope."
                    )
                return view(
                    connection, read_run(connection, alias["run_id"])
                ).model_dump(mode="json"), False
            prior = connection.execute(
                "SELECT body FROM agent_runs WHERE id=?", (run_id,)
            ).fetchone()
            if prior:
                data = StoredRun.model_validate_json(prior["body"])
                if data.scope != scope:
                    raise ClaimError(
                        409, "Run ID is already used with a different scope."
                    )
                return view(connection, data).model_dump(mode="json"), False
            stored_runs = [
                StoredRun.model_validate_json(row["body"])
                for row in connection.execute(
                    "SELECT body FROM agent_runs ORDER BY position"
                )
            ]
            if any(run.status == "running" for run in stored_runs):
                raise ClaimError(409, "Another agent run is active.")
            interrupted = next(
                (
                    run
                    for run in stored_runs
                    if run.status == "interrupted" and run.summary is None
                ),
                None,
            )
            if interrupted:
                if scope.mode != "all" and scope != interrupted.scope:
                    raise ClaimError(
                        409,
                        "Continue the saved scope with uv run -m agent before "
                        "selecting a different cap or claim.",
                    )
                interrupted.status = "running"
                interrupted.execution_generation += 1
                interrupted.finish_input = None
                save_run(connection, interrupted)
                connection.execute(
                    "INSERT INTO run_requests (request_id,run_id,scope) VALUES (?,?,?)",
                    (run_id, interrupted.run_id, scope.model_dump_json()),
                )
                return view(connection, interrupted).model_dump(mode="json"), False
            snapshot = self._snapshot(connection, scope)
            limit = (
                scope.max_claims if isinstance(scope, LimitedScope) else len(snapshot)
            )
            data = StoredRun(
                run_id=run_id,
                scope=scope,
                status="running",
                claim_limit=min(limit, len(snapshot)),
                snapshot_claim_ids=snapshot,
                admitted_claim_ids=[],
                todos=[],
                started_at=now(),
                finished_at=None,
                summary=None,
            )
            save_run(connection, data)
            return view(connection, data).model_dump(mode="json"), True

    @staticmethod
    def _snapshot(connection: Connection, scope: Scope) -> list[str]:
        if scope.mode == "claim":
            row = claim_row(connection, scope.claim_id)
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

    def todos(self, run_id: str, request: TodosInput) -> dict:
        with self.store.transaction() as connection:
            data = read_run(connection, run_id)
            require_running(data)
            self.require_generation(data)
            if len(request.items) > data.claim_limit:
                raise ClaimError(409, "To-do list exceeds the run claim limit.")
            items = [
                Todo(**item.model_dump(), handoff_reason=None) for item in request.items
            ]
            self._validate_todos(connection, data, items)
            data.todos = items
            sync_todos(connection, data)
            save_run(connection, data)
            return dict(
                run_id=run_id,
                items=[item.model_dump(mode="json") for item in data.todos],
                updated_at=now(),
            )

    @staticmethod
    def _validate_todos(connection: Connection, data: StoredRun, items: list[Todo]):
        by_claim = {item.claim_id: item for item in items}
        for old in data.todos:
            if old.claim_id in data.admitted_claim_ids:
                new = by_claim.get(old.claim_id)
                if new is None or new.id != old.id:
                    raise ClaimError(
                        409, "To-do list does not match stored claim progress."
                    )
        for item in items:
            row = claim_row(connection, item.claim_id)
            if item.claim_id not in data.snapshot_claim_ids:
                raise ClaimError(409, "Claim is outside this run's scope.")
            if item.status != progress(row)[0]:
                raise ClaimError(
                    409, "To-do list does not match stored claim progress."
                )

    def start(self, run_id: str, request: AttemptInput) -> tuple[dict, bool]:
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
                return Attempt.model_validate_json(prior["body"]).model_dump(
                    mode="json"
                ), False
            self.require_generation(data)
            require_running(data)
            previous = (
                read_attempt(connection, row["thread_id"])
                if row["thread_id"] and row["run_id"] == run_id
                else None
            )
            if previous and previous.status == "suspended":
                return previous.model_dump(mode="json"), False
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
            entry = Attempt(
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
                **claim_result(claim_row(connection, row["id"])).model_dump(),
            )
            connection.execute(
                "INSERT INTO agent_attempts (thread_id,run_id,claim_id,body,"
                "finish_input,position) VALUES (?, ?, ?, ?, NULL, ?)",
                (
                    thread,
                    run_id,
                    row["id"],
                    entry.model_dump_json(),
                    connection.next_position(attempts_table),
                ),
            )
            sync_todos(connection, data)
            save_run(connection, data)
            return entry.model_dump(mode="json"), True

    @staticmethod
    def _admit(connection: Connection, data: StoredRun, row: Row):
        if row["id"] not in data.snapshot_claim_ids:
            raise ClaimError(409, "Claim is outside this run's scope.")
        if not eligible(row):
            raise ClaimError(409, "Claim is not eligible for assessment.")
        if connection.execute("SELECT 1 FROM claims WHERE active=1").fetchone():
            raise ClaimError(409, "Another claim is already running.")
        if row["id"] not in data.admitted_claim_ids:
            if len(data.admitted_claim_ids) >= data.claim_limit:
                raise ClaimError(409, "Run claim limit reached.")
            data.admitted_claim_ids.append(row["id"])
        if not any(item.claim_id == row["id"] for item in data.todos):
            raise ClaimError(409, "To-do list does not match stored claim progress.")

    def finish_attempt(self, run_id: str, thread: str, error: str) -> dict:
        with self.store.transaction() as connection:
            data = read_run(connection, run_id)
            entry = self._finish_attempt(
                connection, data, thread, error, self.execution_id
            )
            if data.status == "running":
                sync_todos(connection, data)
                save_run(connection, data)
            return entry.model_dump(mode="json")

    def suspend_attempt(self, run_id: str, thread: str, detail: str) -> dict:
        with self.store.transaction() as connection:
            entry = read_attempt(connection, thread)
            if entry.run_id != run_id:
                raise ClaimError(409, "Assessment does not belong to this run.")
            if (
                entry.execution_id
                and self.execution_id is not None
                and self.execution_id != entry.execution_id
            ):
                raise ClaimError(409, "Claim execution has been superseded.")
            return suspend(connection, entry, detail).model_dump(mode="json")

    @staticmethod
    def _finish_attempt(
        connection: Connection,
        data: StoredRun,
        thread: str,
        error: str,
        execution_id: str | None = None,
    ) -> Attempt:
        stored = connection.execute(
            "SELECT * FROM agent_attempts WHERE thread_id=? AND run_id=?",
            (thread, data.run_id),
        ).fetchone()
        if stored is None:
            raise ClaimError(404, "Claim attempt not found.")
        entry = Attempt.model_validate_json(stored["body"])
        if entry.status in {"paused", "suspended"} and not error:
            return entry
        if (
            entry.execution_id
            and execution_id is not None
            and execution_id != entry.execution_id
        ):
            raise ClaimError(409, "Claim execution has been superseded.")
        if entry.status != "active":
            if stored["finish_input"] != error:
                raise ClaimError(
                    409, "Attempt request conflicts with the recorded attempt."
                )
            return entry
        row = claim_row(connection, entry.claim_id)
        if row["thread_id"] != thread or not row["active"]:
            raise ClaimError(409, "Claim attempt is no longer active.")
        if entry.deadline_at <= now() and not error:
            raise ClaimError(409, "Claim attempt is no longer active.")
        result = claim_result(row)
        failure = error or ("No decision was recorded." if not row["decision"] else "")
        if result.payment_status == "eligible" and not failure:
            failure = "Approved payment was not recorded."
        Runs._close_claim(connection, row, failure)
        entry = Attempt.model_validate(
            entry.model_dump()
            | {
                "status": "failed" if failure else "completed",
                "error": failure or None,
                "finished_at": now(),
                **claim_result(claim_row(connection, row["id"])).model_dump(),
            }
        )
        connection.execute(
            "UPDATE agent_attempts SET body=?, finish_input=? WHERE thread_id=?",
            (entry.model_dump_json(), error, thread),
        )
        return entry

    @staticmethod
    def _close_claim(connection: Connection, row: Row, failure: str):
        failures = row["failed_attempts"] + int(not row["decision"])
        state, reason = row["state"], None
        if not row["decision"]:
            state = "handler_review" if failures >= MAX_FAILED_ATTEMPTS else "waiting"
            reason = (
                "two_failed_assessments" if failures >= MAX_FAILED_ATTEMPTS else None
            )
        elif claim_result(row).payment_status == "eligible":
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

    def check(self, run_id: str) -> dict:
        with self.store.connect() as connection:
            data = read_run(connection, run_id)
            if data.status == "running":
                sync_todos(connection, data)
            return completion(connection, data).model_dump(mode="json")

    def finish(self, run_id: str, request: RunFinishInput) -> dict:
        with self.store.transaction() as connection:
            data = read_run(connection, run_id)
            self.require_generation(data)
            if request.status == "interrupted" and data.status in {
                "running",
                "interrupted",
            }:
                self._suspend_run(connection, data, request)
                return view(connection, data).model_dump(mode="json")
            if data.status != "running":
                if data.finish_input != request:
                    raise ClaimError(
                        409, "Run finish conflicts with the recorded result."
                    )
                return view(connection, data).model_dump(mode="json")
            if request.status != "completed":
                for entry in attempts(connection, run_id):
                    if entry.status == "active":
                        self._finish_attempt(
                            connection, data, entry.thread_id, request.detail
                        )
            sync_todos(connection, data)
            check = completion(connection, data)
            if request.status == "completed" and not check.complete:
                raise ClaimError(409, "Run has unfinished work.")
            totals = counts(connection, data)
            text = (
                f"Worked {totals.worked} claims in "
                f"{totals.assessment_attempts} attempts: "
                f"{totals.approved} approved, {totals.declined} declined, "
                f"{totals.referred} referred. "
                f"{totals.handler_review} need handler review; "
                f"{totals.awaiting_sign_off} await sign-off; "
                f"{totals.waiting_remaining} remain waiting."
            )
            data.status = request.status
            data.finished_at = now()
            data.counts = totals
            data.summary = Summary(text=text, detail=request.detail, counts=totals)
            data.final_check = check
            data.finish_input = request
            data.final_attempts = attempts(connection, run_id)
            save_run(connection, data)
            return view(connection, data).model_dump(mode="json")

    @staticmethod
    def _suspend_run(
        connection: Connection, data: StoredRun, request: RunFinishInput
    ) -> None:
        if data.status != "running":
            return
        for entry in attempts(connection, data.run_id):
            if entry.status == "active":
                suspend(connection, entry, request.detail)
        data.status = "interrupted"
        data.finish_input = request
        sync_todos(connection, data)
        save_run(connection, data)

    def _recover_abandoned_runs(self) -> None:
        with self.store.transaction() as connection:
            for row in connection.execute("SELECT body FROM agent_runs"):
                data = StoredRun.model_validate_json(row["body"])
                entries = attempts(connection, data.run_id)
                if (
                    data.status == "running"
                    and any(a.status == "suspended" for a in entries)
                    and not any(a.status == "active" for a in entries)
                ):
                    data.status = "interrupted"
                    sync_todos(connection, data)
                    save_run(connection, data)

    def recover(self, detail: str = "Backend restarted during this run.") -> None:
        with self.store.connect() as connection:
            ids = [
                row["id"]
                for row in connection.execute("SELECT id FROM agent_runs")
                if read_run(connection, row["id"]).status == "running"
            ]
        for run_id in ids:
            self.finish(run_id, RunFinishInput(status="interrupted", detail=detail))
