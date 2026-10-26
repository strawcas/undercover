"""HTTP access to indexed business rules and audited helper reads."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from backend.harness_models import ApiError
from backend.reference_models import (
    AgentPaymentInput,
    Contents,
    Evidence,
    EvidenceResponse,
    PaymentResponse,
    ReadInput,
    SearchInput,
    SearchResponse,
    Source,
)
from backend.references import References
from backend.routes import Service as ClaimService

router = APIRouter(
    prefix="/api", responses={404: {"model": ApiError}, 409: {"model": ApiError}}
)


def service(request: Request) -> References:
    return References(request.app.state.claims.store)


Service = Annotated[References, Depends(service)]


@router.get(
    "/reference-documents/{source}/sections",
    operation_id="list_reference_sections",
    response_model=Contents,
)
def contents(source: Source, references: Service):
    """List stable section IDs for one business document."""
    return references.contents(source)


@router.post(
    "/claims/{claim_id}/reference-search",
    operation_id="search_claim_references",
    response_model=SearchResponse,
)
def search(claim_id: str, body: SearchInput, references: Service):
    """Search sections and record the claim's lookup activity."""
    return references.search(claim_id, body)


@router.post(
    "/claims/{claim_id}/reference-reads",
    operation_id="read_claim_reference",
    response_model=Evidence,
    status_code=201,
    responses={200: {"model": Evidence}},
)
def read(claim_id: str, body: ReadInput, response: Response, references: Service):
    """Read one full section and preserve exactly what the helper received."""
    result, created = references.read(claim_id, body)
    response.status_code = 201 if created else 200
    return result


@router.get(
    "/claims/{claim_id}/evidence",
    operation_id="list_claim_evidence",
    response_model=EvidenceResponse,
)
def evidence(claim_id: str, references: Service):
    """Read the claim's section evidence in first-read order."""
    return references.evidence(claim_id)


@router.post(
    "/claims/{claim_id}/agent-pay",
    operation_id="pay_claim_as_agent",
    response_model=PaymentResponse,
    status_code=201,
)
def agent_pay(claim_id: str, body: AgentPaymentInput, claims: ClaimService):
    """Pay the exact approved amount from the active claim attempt."""
    return claims.pay(claim_id, body.amount, str(body.thread_id))
