"""HTTP operations for the claims pilot."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from backend.claims import Claims
from backend.models import DecisionInput, EventInput, PaymentInput, SignOffInput

router = APIRouter(prefix="/api")


def service(request: Request) -> Claims:
    return request.app.state.claims


Service = Annotated[Claims, Depends(service)]


@router.get("/health", operation_id="health")
def health(request: Request, response: Response) -> dict[str, str]:
    """Check that the backend is running."""
    response.headers["X-UnderCover-Instance"] = request.app.state.instance_id
    return {"status": "ok"}


@router.get("/claims", operation_id="list_claims")
def list_claims(claims: Service):
    """Read the pilot claims queue."""
    return claims.queue()


@router.get("/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str, claims: Service):
    """Read a complete claim and its linked context."""
    return claims.detail(claim_id)


@router.get("/company", operation_id="get_company")
def get_company(claims: Service):
    """Read company settings used in the pilot."""
    return claims.store.record("settings", "company")


@router.get("/handlers", operation_id="get_handlers")
def get_handlers(claims: Service):
    """List handlers available for payout sign-off."""
    return claims.store.record("settings", "handlers")


@router.get("/policy-wording", operation_id="get_policy_wording")
def get_policy_wording(claims: Service):
    """Read the policy wording and its version."""
    return {
        "version": claims.store.record("settings", "company")["policy_wording_version"],
        "text": claims.store.record("settings", "wording"),
    }


@router.get("/agent-instructions", operation_id="get_agent_instructions")
def get_agent_instructions(claims: Service):
    """Read the instructions used for agent runs."""
    return {"text": claims.store.record("settings", "instructions")}


@router.get("/claims/{claim_id}/events", operation_id="list_claim_events")
def list_claim_events(claim_id: str, claims: Service):
    """Read ordered activity for a claim."""
    return claims.events(claim_id)


@router.post(
    "/claims/{claim_id}/events", operation_id="record_claim_event", status_code=201
)
def record_claim_event(claim_id: str, event: EventInput, claims: Service):
    """Record an observable agent action."""
    claims.event(claim_id, event)
    return {"status": "recorded"}


@router.post(
    "/claims/{claim_id}/decision", operation_id="record_decision", status_code=201
)
def record_decision(claim_id: str, decision: DecisionInput, claims: Service):
    """Record a claim decision and calculated payout."""
    return claims.decide(claim_id, decision)


@router.post(
    "/claims/{claim_id}/sign-off", operation_id="sign_off_claim", status_code=201
)
def sign_off_claim(claim_id: str, sign_off: SignOffInput, claims: Service):
    """Sign off the stored approved payout as a handler."""
    return claims.sign_off(claim_id, sign_off.handler_id)


@router.post("/claims/{claim_id}/pay", operation_id="pay_claim", status_code=201)
def pay_claim(claim_id: str, payment: PaymentInput, claims: Service):
    """Record a simulated payment for the approved amount."""
    return claims.pay(claim_id, payment.amount)
