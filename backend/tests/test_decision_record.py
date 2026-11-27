"""Validate complete decisions against the calculation for their own attempt."""

from datetime import datetime, timedelta

import pytest

from backend.models import SECTION_14
from backend.tests.claim_requests import decision_input, fail, start

CLAIM = "CLM-1001"


def payout_request(client, amount, thread_id):
    return client.post(
        f"/api/claims/{CLAIM}/payout",
        json={
            "benefit": "trip_delay",
            "thread_id": thread_id,
            "paid_by_others": 0,
            "lines": [
                {
                    "description": "Taxi",
                    "amount": amount,
                    "quantity": 1,
                    "kind": "expense",
                    "nights": 0,
                    "hours": 0,
                }
            ],
        },
    )


def calculate(client, amount):
    claim = client.get(f"/api/claims/{CLAIM}").json()
    return payout_request(client, amount, claim["thread_id"]).raise_for_status().json()


def submit(client, body, claim_id=CLAIM):
    return client.post(f"/api/claims/{claim_id}/decision", json=body)


@pytest.mark.parametrize("field", ["reason", "section", "decided_by"])
@pytest.mark.parametrize("value", [None, "", " \t"])
def test_required_fields_fail_before_claim_lookup(client, field, value):
    body = decision_input("refer", 0)
    if value is None:
        del body[field]
    else:
        body[field] = value
    existing = submit(client, body)
    missing = submit(client, body, "CLM-9999")
    assert existing.status_code == missing.status_code == 422
    assert existing.json() == missing.json()
    assert f"{SECTION_14}: {field}" in existing.text


@pytest.mark.parametrize(
    ("decision", "payout"),
    [("approve", 0), ("approve", 0.004), ("decline", 20), ("refer", 20)],
)
def test_invalid_payouts_fail_before_claim_lookup(client, decision, payout):
    body = decision_input(decision, payout)
    response = submit(client, body)
    assert response.status_code == 422
    assert response.json() == submit(client, body, "CLM-9999").json()
    assert SECTION_14 in response.text


@pytest.mark.parametrize("field", ["decision", "payout"])
def test_missing_decision_or_payout_names_section_14(client, field):
    body = decision_input("refer", 0)
    del body[field]
    result = submit(client, body, "CLM-9999")
    assert result.status_code == 422
    assert f"{SECTION_14}: {field} is required" in result.text


def refuse_approval(client, amount, explanation):
    response = submit(client, decision_input("approve", amount))
    assert response.status_code == 422
    assert SECTION_14 in response.json()["detail"]
    assert explanation in response.json()["detail"]
    claim = client.get(f"/api/claims/{CLAIM}").json()
    assert claim["decision"] is None
    assert claim["payment"] is None


def test_latest_calculation_is_required_and_preserved_for_the_attempt(client):
    calculate(client, 80)
    start(client, CLAIM)
    refuse_approval(client, 80, "needs a calculation")
    calculate(client, 80)
    fail(client, CLAIM)
    start(client, CLAIM)
    refuse_approval(client, 80, "needs a calculation")

    calculate(client, 80)
    latest = calculate(client, 90)
    refuse_approval(client, 80, "calculated payout of $90.00")
    body = decision_input(
        "approve", 90, decided_by="UnderCover Agent (configuration 7)"
    )
    result = submit(client, body)
    result.raise_for_status()
    decision = result.json()["decision"]
    assert decision["calculation"] == {
        "calculation": latest["calculation"],
        "payout": latest["payout"],
    }
    assert decision["decided_by"] == body["decided_by"]
    assert decision["manual_version"] == "2026.2"
    assert datetime.fromisoformat(decision["decided_at"]).utcoffset() == timedelta(0)
    calculate(client, 100)
    repeated = submit(client, body | {"reason": "Changed after recording"})
    assert repeated.json()["decision"] == decision


def test_decline_has_no_calculation_even_when_the_agent_asked_for_one(client):
    start(client, CLAIM)
    calculate(client, 90)
    decision = submit(client, decision_input("decline", 0)).json()["decision"]
    assert decision["calculation"] is None
    assert decision["payout"] == 0
    assert decision["decided_by"] == "Test handler"
    assert decision["manual_version"] == "2026.2"


def test_delayed_calls_cannot_write_evidence_or_a_decision_into_a_new_attempt(client):
    old, current = "previous-thread", "current-thread"
    for thread in (old, current):
        if thread == current:
            fail(client, CLAIM).raise_for_status()
        client.post(
            f"/api/claims/{CLAIM}/start", json={"thread_id": thread}
        ).raise_for_status()
        payout_request(client, 90, thread).raise_for_status()
    assert payout_request(client, 80, old).status_code == 409
    assert (
        submit(client, decision_input("approve", 90, thread_id=old)).status_code == 409
    )
    approved = submit(client, decision_input("approve", 90, thread_id=current))
    assert approved.json()["decision"]["calculation"]["payout"] == 90
