"""Requests the backend's tests send to work a claim."""

from backend.tests.auth_support import authorization


def start(client, claim_id):
    return client.post(f"/api/claims/{claim_id}/start", json={"thread_id": "t-1"})


def decide(client, claim_id, decision, payout):
    return client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": decision,
            "payout": payout,
            "section": "Section 3, Trip Delay",
            "reason": "The delay was covered.",
        },
    )


def pay(client, claim_id, amount):
    return client.post(f"/api/claims/{claim_id}/payment", json={"amount": amount})


def sign_off(client, claim_id, handler_id):
    return client.post(
        f"/api/claims/{claim_id}/sign-off", headers=authorization(handler_id)
    )


def fail(client, claim_id, reason="The model timed out"):
    return client.post(f"/api/claims/{claim_id}/failure", json={"reason": reason})
