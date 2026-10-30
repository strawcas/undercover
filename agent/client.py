"""The terminal submits work to the single agent service and reports saved state."""

from time import sleep
from uuid import uuid4

import httpx
from pydantic import TypeAdapter

from agent.execution import AgentRunError, AttemptResult, Execution, Runner
from agent.local_process import Settings, local_service
from agent.main import backend
from agent.service_worker import ModelFactory
from backend.claim_models import ClaimDetail
from backend.config import agent_url, backend_url, database_identity, database_target
from backend.diagnostics import diagnose, preserve_primary
from backend.harness_models import Attempt, Run, Scope


def report_attempts(
    client: httpx.Client, entries: list[Attempt], execution: Execution, seen: set[str]
) -> None:
    for entry in entries:
        if (
            entry.status not in {"completed", "failed", "paused"}
            or entry.thread_id in seen
        ):
            continue
        seen.add(entry.thread_id)
        try:
            response = client.get(f"/api/claims/{entry.claim_id}")
            response.raise_for_status()
            claim = ClaimDetail.model_validate(response.json())
            execution.on_result(AttemptResult(entry, claim))
        except Exception as error:
            diagnose("Report service assessment", error)


def report_continuations(
    client: httpx.Client, saved: list[Attempt], execution: Execution, seen: set[str]
) -> None:
    for previous in saved:
        if previous.thread_id in seen or previous.run_id == execution.run_id:
            continue
        try:
            response = client.get(f"/api/claims/{previous.claim_id}/execution")
            response.raise_for_status()
            entry = Attempt.model_validate(response.json())
        except Exception as error:
            diagnose("Read continued assessment result", error)
            continue
        if (
            entry.thread_id == previous.thread_id
            and entry.status == "completed"
            and (entry.outcome != "approve" or entry.payment_status == "paid")
        ):
            report_attempts(client, [entry], execution, seen)
            execution.resumed_count += 1


def follow(
    client: httpx.Client, current: Run, execution: Execution, saved: list[Attempt]
) -> Run:
    seen: set[str] = set()
    while True:
        execution.result = current
        report_attempts(client, current.attempts, execution, seen)
        report_continuations(client, saved, execution, seen)
        if current.status != "running":
            if current.status == "failed" and current.summary:
                raise AgentRunError(current.summary.detail)
            return current
        sleep(0.15)
        response = client.get(f"/api/agent-runs/{current.run_id}")
        response.raise_for_status()
        current = Run.model_validate(response.json())


def command(
    scope: dict,
    execution: Execution,
    runner: Runner | None = None,
    model_factory: ModelFactory | None = None,
) -> Run:
    """Use the existing service or temporary services without opening a CLI pool."""
    identity = database_identity(database_target())
    selected = TypeAdapter(Scope).validate_python(scope)
    with (
        local_service(Settings("backend", backend_url()), identity),
        local_service(Settings("agent", agent_url(), runner, model_factory), identity),
        httpx.Client(base_url=agent_url(), timeout=30) as agent,
        backend() as client,
    ):
        requested = str(uuid4())
        pending = client.get("/api/claim-executions/pending")
        pending.raise_for_status()
        saved = TypeAdapter(list[Attempt]).validate_python(pending.json())
        response = agent.post(
            "/runs",
            json={"run_id": requested, "scope": selected.model_dump(mode="json")},
        )
        if response.is_error:
            detail = response.json().get("detail")
            raise AgentRunError(
                detail
                if isinstance(detail, str)
                else "The agent service rejected the command."
            )
        current = Run.model_validate(response.json())
        execution.run_id = current.run_id
        execution.continued = current.run_id != requested
        try:
            return follow(client, current, execution, saved)
        except KeyboardInterrupt:
            with preserve_primary("Suspend terminal service command"):
                agent.post(f"/runs/{current.run_id}/stop").raise_for_status()
                for _ in range(40):
                    stored = client.get(f"/api/agent-runs/{current.run_id}")
                    stored.raise_for_status()
                    execution.result = Run.model_validate(stored.json())
                    if execution.result.status != "running":
                        break
                    sleep(0.1)
            raise
