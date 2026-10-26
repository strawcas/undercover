"""Queue tools delegate isolated helpers through backend-owned attempts."""

from http import HTTPStatus
from threading import Lock
from typing import Literal
from uuid import uuid4

import httpx
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict

from agent.execution import AgentRunError, Execution, QueueStopped, Record, Runner
from agent.http import request as api_request
from agent.invocation import invoke_helper


class QueueTodo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    claim_id: str
    text: str
    status: Literal["to_do", "in_progress", "done", "left_for_handler"]


class QueueTools:
    """Keep queue history small and claim evidence inside each fresh helper."""

    def __init__(
        self, client: httpx.Client, run_id: str, runner: Runner, execution: Execution
    ):
        self.client = client
        self.run_id = run_id
        self.path = f"/api/agent-runs/{run_id}"
        self.runner = runner
        self.execution = execution
        self.lock = Lock()

    def request(self, method: str, path: str, body: Record | None = None) -> Record:
        response = api_request(self.client, method, path, body)
        response.raise_for_status()
        return response.json()

    def check(self) -> Record:
        return self.request("GET", f"{self.path}/completion-check")

    def finish_attempt(self, thread_id: str, error: str) -> Record:
        response = api_request(
            self.client,
            "POST",
            f"{self.path}/attempts/{thread_id}/finish",
            {"error": error},
        )
        if response.status_code == HTTPStatus.CONFLICT:
            current = self.request("GET", self.path)
            if current["status"] != "running":
                self.execution.result = current
                self.execution.stopping.set()
                return next(
                    entry
                    for entry in current["attempts"]
                    if entry["thread_id"] == thread_id
                )
        response.raise_for_status()
        return response.json()

    def waiting(self) -> Record:
        check = self.check()
        run = self.request("GET", self.path)
        response = api_request(self.client, "GET", "/api/claims")
        response.raise_for_status()
        allowed = set(check["missing_claim_ids"] + check["retryable_claim_ids"])
        return dict(
            run_id=self.run_id,
            capacity_remaining=check["capacity_remaining"],
            claims=[row for row in response.json() if row["claim_id"] in allowed],
            todos=run["todos"],
            retryable_claim_ids=check["retryable_claim_ids"],
        )

    def delegate(self, claim_id: str) -> str:
        with self.lock:
            if self.execution.stopping.is_set():
                return "Queue processing is stopping; do not start another claim."
            thread_id = str(uuid4())
            response = api_request(
                self.client,
                "POST",
                f"{self.path}/attempts",
                {
                    "claim_id": claim_id,
                    "thread_id": thread_id,
                },
            )
            if response.is_error:
                return f"{claim_id}: {response.json()['detail']}"
            error = ""
            try:
                invoke_helper(self.runner, response.json(), self.execution)
            except QueueStopped:
                error = "Assessment interrupted. Retry in a new session."
                raise
            except AgentRunError as failure:
                error = str(failure)
            except Exception as failure:
                error = (
                    f"Agent run failed ({type(failure).__name__}). "
                    "Assessment can be retried."
                )
            except KeyboardInterrupt:
                error = "Assessment interrupted. Retry in a new session."
                raise
            finally:
                entry = self.finish_attempt(thread_id, error)
                claim = self.request("GET", f"/api/claims/{claim_id}")
                self.execution.on_result({"attempt": entry, "claim": claim})
            if entry["outcome"]:
                return (
                    f"{claim_id} {entry['outcome'].upper()} ${entry['payout']} "
                    f"({entry['payment_status']})."
                    + (f" {entry['error']}" if entry["error"] else "")
                )
            return f"{claim_id} FAILED: {entry['error']}"

    def tools(self) -> list[BaseTool]:
        @tool
        def list_waiting_claims() -> dict:
            """Read eligible claims, capacity and the stored to-do list."""
            return self.waiting()

        @tool
        def write_todos(items: list[QueueTodo]) -> dict:
            """Replace the claim-linked plan using statuses returned by the backend."""
            response = api_request(
                self.client,
                "PUT",
                f"{self.path}/todos",
                {
                    "items": [item.model_dump() for item in items],
                },
            )
            return response.json()

        @tool
        def delegate_claim(claim_id: str) -> str:
            """Give one eligible claim to a fresh helper and receive one result line."""
            return self.delegate(claim_id)

        @tool
        def check_completion() -> dict:
            """Check for missed or retryable work before returning the final summary."""
            return self.check()

        return [list_waiting_claims, write_todos, delegate_claim, check_completion]
