"""Queue tools reserve attempts, invoke fresh helpers and report saved outcomes."""

from http import HTTPStatus
from threading import Lock
from uuid import uuid4

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, TypeAdapter

from agent.execution import (
    AgentRunError,
    AttemptResult,
    Execution,
    QueueStopped,
    Runner,
)
from agent.http import request
from agent.invocation import invoke_helper
from backend.claim_models import ClaimDetail, QueueRow
from backend.diagnostics import diagnose, preserve_primary
from backend.harness_models import Attempt, CompletionCheck, Run, Todo, TodoInput


class WaitingClaims(BaseModel):
    run_id: str
    capacity_remaining: int
    claims: list[QueueRow]
    todos: list[Todo]
    retryable_claim_ids: list[str]


class QueueTools:
    """Keep claim evidence inside each helper and workload enforcement in the API."""

    def __init__(
        self, client: httpx.Client, run_id: str, runner: Runner, execution: Execution
    ):
        self.client = client
        self.run_id = run_id
        self.path = f"/api/agent-runs/{run_id}"
        self.runner = runner
        self.execution = execution
        self.lock = Lock()

    def check(self) -> CompletionCheck:
        response = request(self.client, "GET", f"{self.path}/completion-check")
        response.raise_for_status()
        return CompletionCheck.model_validate(response.json())

    def current(self) -> Run:
        response = request(self.client, "GET", self.path)
        response.raise_for_status()
        return Run.model_validate(response.json())

    def finish_run(self, status: str, detail: str) -> Run:
        response = request(
            self.client,
            "POST",
            f"{self.path}/finish",
            {"status": status, "detail": detail},
        )
        if response.status_code == HTTPStatus.CONFLICT:
            current = self.current()
            if current.status != "running":
                return current
        response.raise_for_status()
        return Run.model_validate(response.json())

    def finish_attempt(self, thread_id: str, error: str) -> Attempt:
        response = request(
            self.client,
            "POST",
            f"{self.path}/attempts/{thread_id}/finish",
            {"error": error},
        )
        if response.status_code == HTTPStatus.CONFLICT:
            current = self.current()
            if current.status != "running":
                self.execution.result = current
                self.execution.stopping.set()
                return next(
                    entry for entry in current.attempts if entry.thread_id == thread_id
                )
        response.raise_for_status()
        return Attempt.model_validate(response.json())

    def waiting(self) -> dict:
        """Read eligible claims, capacity and the stored to-do list."""
        check, run = self.check(), self.current()
        response = request(self.client, "GET", "/api/claims")
        response.raise_for_status()
        allowed = set(check.missing_claim_ids + check.retryable_claim_ids)
        claims = TypeAdapter(list[QueueRow]).validate_python(response.json())
        return WaitingClaims(
            run_id=self.run_id,
            capacity_remaining=check.capacity_remaining,
            claims=[row for row in claims if row.claim_id in allowed],
            todos=run.todos,
            retryable_claim_ids=check.retryable_claim_ids,
        ).model_dump(mode="json")

    def write_todos(self, items: list[TodoInput]) -> dict:
        """Replace the claim-linked plan using statuses returned by the backend."""
        response = request(
            self.client,
            "PUT",
            f"{self.path}/todos",
            {"items": [item.model_dump(mode="json") for item in items]},
        )
        return response.json()

    def report(self, entry: Attempt) -> None:
        try:
            response = request(self.client, "GET", f"/api/claims/{entry.claim_id}")
            response.raise_for_status()
            claim = ClaimDetail.model_validate(response.json())
            self.execution.on_result(AttemptResult(entry, claim))
        except Exception as error:
            diagnose("Report saved assessment", error)

    def conclude(
        self, entry: Attempt, error: str, primary: Exception | None = None
    ) -> str:
        try:
            saved = self.finish_attempt(entry.thread_id, error)
        except Exception as cleanup_error:
            if primary is None:
                raise
            reference = diagnose("Finalize failed assessment", cleanup_error)
            primary.add_note(f"Attempt cleanup also failed. {reference}.")
            raise AgentRunError(error) from primary
        self.report(saved)
        if saved.outcome:
            return (
                f"{saved.claim_id} {saved.outcome.upper()} ${saved.payout} "
                f"({saved.payment_status})."
                + (f" {saved.error}" if saved.error else "")
            )
        return f"{saved.claim_id} FAILED: {saved.error}"

    def delegate(self, claim_id: str) -> str:
        """Give one eligible claim to a fresh helper and receive one result line."""
        with self.lock:
            if self.execution.stopping.is_set():
                return "Queue processing is stopping; do not start another claim."
            response = request(
                self.client,
                "POST",
                f"{self.path}/attempts",
                {"claim_id": claim_id, "thread_id": str(uuid4())},
            )
            if response.is_error:
                return f"{claim_id}: {response.json()['detail']}"
            entry = Attempt.model_validate(response.json())
            try:
                invoke_helper(self.runner, entry, self.execution)
            except (QueueStopped, KeyboardInterrupt):
                with preserve_primary("Finalize interrupted assessment"):
                    self.finish_attempt(
                        entry.thread_id,
                        "Assessment interrupted. "
                        "Retry eligible work with uv run -m agent.",
                    )
                raise
            except AgentRunError as failure:
                return self.conclude(entry, str(failure), failure)
            except Exception as failure:
                reference = diagnose("Assess claim", failure)
                error = f"Agent run failed ({type(failure).__name__}). {reference}."
                return self.conclude(entry, error, failure)
            return self.conclude(entry, "")

    def check_completion(self) -> dict:
        """Check for missed or retryable work before returning the final summary."""
        return self.check().model_dump(mode="json")

    def tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(self.waiting, name="list_waiting_claims"),
            StructuredTool.from_function(self.write_todos),
            StructuredTool.from_function(self.delegate, name="delegate_claim"),
            StructuredTool.from_function(self.check_completion),
        ]
