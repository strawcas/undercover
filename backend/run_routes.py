"""HTTP state operations for the local harness."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from backend.harness_models import (
    ApiError,
    Attempt,
    AttemptFinishInput,
    AttemptInput,
    CompletionCheck,
    CurrentRun,
    Run,
    RunFinishInput,
    RunInput,
    TodosInput,
    TodosResponse,
)
from backend.runs import Runs

router = APIRouter(
    prefix="/api/agent-runs",
    responses={
        404: {"model": ApiError},
        409: {"model": ApiError},
    },
)


def service(request: Request) -> Runs:
    return Runs(request.app.state.claims.store)


Service = Annotated[Runs, Depends(service)]


@router.post(
    "",
    operation_id="create_agent_run",
    response_model=Run,
    status_code=201,
    responses={200: {"model": Run}},
)
def create_run(body: RunInput, response: Response, runs: Service):
    """Register a finite workload without invoking an agent."""
    result, created = runs.create(body)
    response.status_code = 201 if created else 200
    return result


@router.get("/current", operation_id="get_current_agent_run", response_model=CurrentRun)
def current_run(runs: Service):
    """Read the current or most recently registered run."""
    return {"run": runs.current()}


@router.get("/{run_id}", operation_id="get_agent_run", response_model=Run)
def get_run(run_id: UUID, runs: Service):
    """Read the workload, progress and attempts for one run."""
    return runs.get(str(run_id))


@router.put(
    "/{run_id}/todos", operation_id="update_run_todos", response_model=TodosResponse
)
def update_todos(run_id: UUID, body: TodosInput, runs: Service):
    """Replace the agent's plan while preserving admitted claim progress."""
    return runs.todos(str(run_id), body)


@router.post(
    "/{run_id}/attempts",
    operation_id="start_claim_attempt",
    response_model=Attempt,
    status_code=201,
    responses={200: {"model": Attempt}},
)
def start_attempt(run_id: UUID, body: AttemptInput, response: Response, runs: Service):
    """Reserve the requested claim within the run's scope and cap."""
    result, created = runs.start(str(run_id), body)
    response.status_code = 201 if created else 200
    return result


@router.post(
    "/{run_id}/attempts/{thread_id}/finish",
    operation_id="finish_claim_attempt",
    response_model=Attempt,
)
def finish_attempt(
    run_id: UUID, thread_id: UUID, body: AttemptFinishInput, runs: Service
):
    """Finalize an assessment and recover an undecided failure."""
    return runs.finish_attempt(str(run_id), str(thread_id), body.error)


@router.get(
    "/{run_id}/completion-check",
    operation_id="check_run_completion",
    response_model=CompletionCheck,
)
def check_completion(run_id: UUID, runs: Service):
    """Find active, missed or retryable claims before finishing."""
    return runs.check(str(run_id))


@router.post("/{run_id}/finish", operation_id="finish_agent_run", response_model=Run)
def finish_run(run_id: UUID, body: RunFinishInput, runs: Service):
    """Close a run and store its factual handover summary."""
    return runs.finish(str(run_id), body)
