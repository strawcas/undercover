"""Requests the backend's tests send to work a claim."""

from sqlalchemy import update

from backend.database import claims, engine
from backend.tests.auth_support import authorization


def start(client, claim_id):
    return client.post(f"/api/claims/{claim_id}/start", json={"thread_id": "t-1"})


def decision_input(decision, payout, **fields):
    return {
        "decision": decision,
        "payout": payout,
        "section": "Section 3, Trip Delay",
        "reason": "The delay was covered.",
        "decided_by": "Test handler",
        "thread_id": "t-1",
    } | fields


def decide(client, claim_id, decision, payout, **fields):
    claim = client.get(f"/api/claims/{claim_id}").json()
    if thread_id := claim.get("thread_id"):
        fields["thread_id"] = thread_id
    if decision == "approve" and round(payout, 2) > 0:
        with engine().begin() as db:
            amount = round(payout, 2)
            evidence = {
                "calculation": [{"step": "Covered amount", "amount": amount}],
                "payout": amount,
            }
            db.execute(
                update(claims)
                .where(claims.c.claim_id == claim_id, claims.c.status == "in_progress")
                .values(latest_calculation=evidence)
            )
    return client.post(
        f"/api/claims/{claim_id}/decision",
        json=decision_input(decision, payout, **fields),
    )


def pay(client, claim_id, amount):
    return client.post(f"/api/claims/{claim_id}/payment", json={"amount": amount})


def sign_off(client, claim_id, handler_id):
    return client.post(
        f"/api/claims/{claim_id}/sign-off", headers=authorization(handler_id)
    )


def fail(client, claim_id, reason="The model timed out"):
    return client.post(f"/api/claims/{claim_id}/failure", json={"reason": reason})
