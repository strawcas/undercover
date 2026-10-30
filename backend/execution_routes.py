"""HTTP ownership and continuation of saved claim assessments."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from backend.executions import ExecutionInput, Executions, ReleaseInput
from backend.harness_models import Attempt

router = APIRouter(prefix="/api")


def service(request: Request) -> Executions:
    return Executions(request.app.state.claims.store)


Service = Annotated[Executions, Depends(service)]


@router.get(
    "/claim-executions/pending",
    operation_id="list_resumable_assessments",
    response_model=list[Attempt],
)
def pending(executions: Service):
    """List stopped or signed-off assessments that can continue."""
    return executions.pending()


@router.post(
    "/claims/{claim_id}/execution",
    operation_id="acquire_claim_execution",
    response_model=Attempt,
)
def acquire(claim_id: str, body: ExecutionInput, executions: Service):
    """Acquire a generation-fenced execution of the saved assessment."""
    return executions.acquire(claim_id, body)


@router.put(
    "/claims/{claim_id}/execution/{execution_id}",
    operation_id="heartbeat_claim_execution",
    response_model=Attempt,
)
def heartbeat(
    claim_id: str, execution_id: UUID, body: ExecutionInput, executions: Service
):
    """Renew ownership while keeping the assessment's active-work deadline."""
    return executions.heartbeat(claim_id, str(execution_id), body)


@router.post(
    "/claims/{claim_id}/execution/{execution_id}/release",
    operation_id="release_claim_execution",
    response_model=Attempt,
)
def release(claim_id: str, execution_id: UUID, body: ReleaseInput, executions: Service):
    """Release execution ownership or suspend work without failing an attempt."""
    return executions.release(claim_id, str(execution_id), body)
