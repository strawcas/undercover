"""HTTP operations for the claims pilot."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from backend.claim_models import (
    ClaimDetail,
    ClaimEvent,
    Decision,
    Health,
    Payment,
    QueueRow,
    Recorded,
    SignOff,
    Wording,
)
from backend.claims import Claims
from backend.harness_models import ApiError
from backend.models import (
    Company,
    DecisionInput,
    EventInput,
    Handler,
    PaymentInput,
    SignOffInput,
)
from backend.reference_models import InstructionsResponse

router = APIRouter(
    prefix="/api", responses={404: {"model": ApiError}, 409: {"model": ApiError}}
)


def service(request: Request) -> Claims:
    return request.app.state.claims


Service = Annotated[Claims, Depends(service)]


@router.get("/health", operation_id="health", response_model=Health)
def health(request: Request, response: Response) -> dict[str, str]:
    """Check that the backend is running."""
    response.headers["X-UnderCover-Instance"] = request.app.state.instance_id
    response.headers["X-UnderCover-Database"] = request.app.state.database_id
    return {"status": "ok"}


@router.get("/claims", operation_id="list_claims", response_model=list[QueueRow])
def list_claims(claims: Service):
    """Read the pilot claims queue."""
    return claims.queue()


@router.get("/claims/{claim_id}", operation_id="get_claim", response_model=ClaimDetail)
def get_claim(claim_id: str, claims: Service):
    """Read a complete claim and its linked context."""
    return claims.detail(claim_id)


@router.get("/company", operation_id="get_company", response_model=Company)
def get_company(claims: Service):
    """Read company settings used in the pilot."""
    return claims.store.record("settings", "company")


@router.get("/handlers", operation_id="get_handlers", response_model=list[Handler])
def get_handlers(claims: Service):
    """List handlers available for payout sign-off."""
    return claims.store.record("settings", "handlers")


@router.get(
    "/policy-wording", operation_id="get_policy_wording", response_model=Wording
)
def get_policy_wording(claims: Service):
    """Read the policy wording and its version."""
    return {
        "version": claims.store.record("settings", "company")["policy_wording_version"],
        "text": claims.store.record("settings", "wording"),
    }


@router.get(
    "/agent-instructions",
    operation_id="get_agent_instructions",
    response_model=InstructionsResponse,
)
def get_agent_instructions(claims: Service):
    """Read the instructions used for agent runs."""
    return {
        "text": claims.store.record("settings", "instructions"),
        "queue_text": claims.store.record("settings", "queue_instructions"),
    }


@router.get(
    "/claims/{claim_id}/events",
    operation_id="list_claim_events",
    response_model=list[ClaimEvent],
)
def list_claim_events(claim_id: str, claims: Service):
    """Read ordered activity for a claim."""
    return claims.events(claim_id)


@router.post(
    "/claims/{claim_id}/events",
    operation_id="record_claim_event",
    status_code=201,
    response_model=Recorded,
)
def record_claim_event(claim_id: str, event: EventInput, claims: Service):
    """Record an observable agent action."""
    claims.validate_thread(claim_id, event.thread_id)
    claims.event(claim_id, event)
    return {"status": "recorded"}


@router.post(
    "/claims/{claim_id}/decision",
    operation_id="record_decision",
    status_code=201,
    response_model=Decision,
)
def record_decision(claim_id: str, decision: DecisionInput, claims: Service):
    """Record a claim decision and calculated payout."""
    claims.validate_thread(claim_id, decision.thread_id)
    return claims.decide(claim_id, decision)


@router.post(
    "/claims/{claim_id}/sign-off",
    operation_id="sign_off_claim",
    status_code=201,
    response_model=SignOff,
)
def sign_off_claim(claim_id: str, sign_off: SignOffInput, claims: Service):
    """Sign off the stored approved payout as a handler."""
    return claims.sign_off(claim_id, sign_off.handler_id)


@router.post(
    "/claims/{claim_id}/pay",
    operation_id="pay_claim",
    status_code=201,
    response_model=Payment,
)
def pay_claim(claim_id: str, payment: PaymentInput, claims: Service):
    """Record a simulated payment for the approved amount."""
    return claims.pay(claim_id, payment.amount)
