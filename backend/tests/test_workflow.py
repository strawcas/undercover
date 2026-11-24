from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from backend import main
from backend.database import claims, decisions, engine, handlers, payments
from backend.tests.claim_requests import decide, decision_input, pay, sign_off, start


def test_start_claim(client):
    response = start(client, "CLM-1001")
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    assert response.json()["thread_id"] == "t-1"
    assert start(client, "CLM-1001").status_code == 409


def test_start_needs_a_thread_id(client):
    response = client.post("/api/claims/CLM-1001/start", json={"thread_id": ""})
    assert response.status_code == 422


def test_steps(client):
    assert (
        client.post(
            "/api/claims/CLM-1001/steps",
            json={"kind": "message", "content": "Hi", "thread_id": "t-1"},
        ).status_code
        == 409
    )
    start(client, "CLM-1001")
    for kind, content in (("tool_call", "get_claim"), ("tool_result", "{}")):
        response = client.post(
            "/api/claims/CLM-1001/steps",
            json={"kind": kind, "content": content, "thread_id": "t-1"},
        )
        assert response.status_code == 200
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert [(s["kind"], s["content"]) for s in steps] == [
        ("tool_call", "get_claim"),
        ("tool_result", "{}"),
    ]
    assert steps[0]["step_id"] < steps[1]["step_id"]
    assert all(s["thread_id"] == "t-1" and s["created_at"] for s in steps)
    assert client.get("/api/claims/CLM-1002/steps").json() == []
    assert client.get("/api/claims/CLM-9999/steps").status_code == 404


def test_step_kind_must_be_known(client):
    start(client, "CLM-1001")
    response = client.post(
        "/api/claims/CLM-1001/steps",
        json={"kind": "thought", "content": "Hmm", "thread_id": "t-1"},
    )
    assert response.status_code == 422


def test_steps_require_the_active_attempt(client):
    start(client, "CLM-1001")
    response = client.post(
        "/api/claims/CLM-1001/steps",
        json={"kind": "message", "content": "Late result", "thread_id": "old"},
    )
    assert response.status_code == 409


def test_decision_needs_a_claim_in_progress(client):
    assert decide(client, "CLM-1001", "decline", 0).status_code == 409
    assert decide(client, "CLM-9999", "decline", 0).status_code == 404


@pytest.mark.parametrize(
    ("decision", "payout", "section"),
    [
        ("approve", 0, "Section 3"),
        ("approve", 0.004, "Section 3"),
        ("decline", 50, "Section 3"),
        ("refer", 10, "Section 3"),
        ("maybe", 0, "Section 3"),
        ("approve", -5, "Section 3"),
        ("decline", 0, ""),
    ],
)
def test_invalid_decisions(client, decision, payout, section):
    start(client, "CLM-1001")
    response = client.post(
        "/api/claims/CLM-1001/decision",
        json=decision_input(decision, payout, section=section, reason="Because."),
    )
    assert response.status_code == 422
    assert client.get("/api/claims/CLM-1001").json()["decision"] is None


def test_approve_and_pay_within_the_limit(client):
    start(client, "CLM-1001")
    claim = decide(client, "CLM-1001", "approve", 420.0).json()
    assert claim["status"] == "in_progress"
    assert claim["decision"]["payout"] == 420.0
    assert claim["payment"] is None
    response = pay(client, "CLM-1001", 420.0)
    assert response.status_code == 200
    assert response.json()["status"] == "decided"
    assert response.json()["payment"]["amount"] == 420.0
    assert response.json()["payment"]["signed_off_by"] is None
    assert client.get("/api/claims/CLM-1001").json()["payment"]["amount"] == 420.0


def test_a_repeated_decision_is_done_and_a_different_one_refused(client):
    start(client, "CLM-1001")
    first = decide(client, "CLM-1001", "approve", 420.0).json()
    again = decide(client, "CLM-1001", "approve", 420.0)
    assert again.status_code == 200
    assert again.json()["decision"] == first["decision"]
    different = decide(client, "CLM-1001", "approve", 400.0)
    assert different.status_code == 409
    assert different.json()["detail"] == "Claim CLM-1001 has already been decided"
    assert decide(client, "CLM-1001", "decline", 0).status_code == 409
    pay(client, "CLM-1001", 420.0)
    assert decide(client, "CLM-1001", "approve", 420.0).json()["status"] == "decided"


def test_a_repeated_payment_is_done_and_a_different_one_refused(client):
    start(client, "CLM-1001")
    decide(client, "CLM-1001", "approve", 420.0)
    paid = pay(client, "CLM-1001", 420.0).json()["payment"]
    again = pay(client, "CLM-1001", 420.0)
    assert again.status_code == 200
    assert again.json()["payment"] == paid
    different = pay(client, "CLM-1001", 400.0)
    assert different.status_code == 409
    assert different.json()["detail"] == "Claim CLM-1001 has already been paid"


def test_concurrent_payment_retries_return_one_payment(client):
    claim_id = "CLM-1001"
    start(client, claim_id)
    decide(client, claim_id, "approve", 420)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(lambda _: pay(client, claim_id, 420), range(2))
    assert first.status_code == second.status_code == 200
    assert first.json()["payment"] == second.json()["payment"]


def test_queue_shows_the_decision(client):
    start(client, "CLM-1001")
    decide(client, "CLM-1001", "approve", 420.0)
    pay(client, "CLM-1001", 420.0)
    first = client.get("/api/claims").json()[0]
    assert (first["status"], first["decision"], first["payout"]) == (
        "decided",
        "approve",
        420.0,
    )


def test_payment_must_match_the_payout(client):
    start(client, "CLM-1001")
    decide(client, "CLM-1001", "approve", 420.0)
    response = pay(client, "CLM-1001", 500.0)
    assert response.status_code == 422
    assert (
        response.json()["detail"] == "The amount must be the approved payout of $420.00"
    )
    assert client.get("/api/claims/CLM-1001").json()["payment"] is None


def test_exactly_the_limit_needs_no_sign_off(client):
    start(client, "CLM-1002")
    claim = decide(client, "CLM-1002", "approve", 1000.0).json()
    assert claim["status"] == "in_progress"
    assert pay(client, "CLM-1002", 1000.0).json()["status"] == "decided"


def test_over_the_limit_waits_for_sign_off(client):
    start(client, "CLM-1002")
    claim = decide(client, "CLM-1002", "approve", 1350.0).json()
    assert claim["status"] == "waiting_for_sign_off"
    response = pay(client, "CLM-1002", 1350.0)
    assert response.status_code == 403
    assert response.json()["detail"] == (
        "A payout over $1,000.00 needs a handler's sign-off"
    )
    assert client.get("/api/claims/CLM-1002").json()["payment"] is None


def test_gateway_hold_requires_the_agent_key_and_an_unpaid_approval(client):
    claim_id = "CLM-1001"
    path = f"/api/claims/{claim_id}/hold-gateway-payment"
    body = {"reason": "Policy denied the payment"}
    assert TestClient(main.app).post(path, json=body).status_code == 401
    assert client.post(path, json=body).status_code == 409
    start(client, claim_id)
    assert client.post(path, json=body).status_code == 409
    assert decide(client, claim_id, "approve", 420).status_code == 200
    assert pay(client, claim_id, 420).status_code == 200
    assert client.post(path, json=body).status_code == 409


def test_limit_is_checked_before_looking_up_the_claim(client):
    start(client, "CLM-1002")
    decide(client, "CLM-1002", "approve", 1350)
    for claim_id in ("CLM-1001", "CLM-1002", "CLM-9999"):
        response = pay(client, claim_id, 1000.01)
        assert response.status_code == 403
        assert (
            response.json()["detail"]
            == "A payout over $1,000.00 needs a handler's sign-off"
        )
    assert pay(client, "CLM-9999", 1000).status_code == 404


def test_sign_off_pays_and_returns_the_one_persisted_payment(client, agent_answers):
    def check_commit(request):
        assert request.url.path == "/api/claims/CLM-1002/resume"
        claim = client.get("/api/claims/CLM-1002").json()
        assert claim["payment"]["amount"] == 1350
        return httpx.Response(202, json={"status": "accepted"})

    agent_answers(check_commit)
    start(client, "CLM-1002")
    decide(client, "CLM-1002", "approve", 1350)
    assert sign_off(client, "CLM-1002", "HND-99").status_code == 403
    response = sign_off(client, "CLM-1002", "HND-01")
    assert response.status_code == 200
    claim = response.json()
    assert claim["status"] == "decided"
    assert claim["payment"]["amount"] == 1350
    assert claim["payment"]["signed_off_by"] == "Dana Whitfield"
    assert claim["payment"]["signed_off_at"] == claim["decision"]["signed_off_at"]
    assert claim["payment"]["paid_at"] == claim["decision"]["signed_off_at"]
    assert client.get("/api/claims/CLM-1002").json() == claim
    assert pay(client, "CLM-1002", 1350).status_code == 403
    assert sign_off(client, "CLM-1002", "HND-01").status_code == 409


@pytest.mark.parametrize("failure", ["connection", "timeout", "refused", "malformed"])
def test_sign_off_survives_a_failed_handoff(client, agent_answers, caplog, failure):
    def fail(request):
        if failure == "connection":
            raise httpx.ConnectError("Connection failed", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Read timed out", request=request)
        if failure == "malformed":
            return httpx.Response(502, json=[])
        return httpx.Response(409, json={"detail": "Claim has no pending interrupt"})

    agent_answers(fail)
    claim_id = "CLM-1002"
    start(client, claim_id)
    decide(client, claim_id, "approve", 1350)
    signed = sign_off(client, claim_id, "HND-01")
    assert signed.status_code == 200
    assert signed.json() == client.get(f"/api/claims/{claim_id}").json()
    assert signed.json()["payment"]["amount"] == 1350
    assert f"Could not resume claim {claim_id} after review" in caplog.text


def test_concurrent_sign_off_settles_once(client):
    claim_id = "CLM-1002"
    start(client, claim_id)
    decide(client, claim_id, "approve", 1350)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(lambda _: sign_off(client, claim_id, "HND-01"), range(2))
        )
    assert sorted(r.status_code for r in replies) == [200, 409]
    with engine().connect() as db:
        assert db.execute(
            select(payments.c.amount).where(payments.c.claim_id == claim_id)
        ).scalars().all() == [1350]


def test_legacy_unpaid_sign_off_preserves_initial_signer(client):
    claim_id = "CLM-1002"
    start(client, claim_id)
    decide(client, claim_id, "approve", 1350)
    previous = "2026-07-20T09:00:00+00:00"
    with engine().begin() as db:
        db.execute(
            update(claims)
            .where(claims.c.claim_id == claim_id)
            .values(status="in_progress")
        )
        db.execute(
            update(decisions)
            .where(decisions.c.claim_id == claim_id)
            .values(
                signed_off_by="Previous senior handler",
                signed_off_at=previous,
            )
        )
    assert client.get(f"/api/claims/{claim_id}").json()["sign_off_available"]
    paid = sign_off(client, claim_id, "HND-01").json()["payment"]
    assert (paid["amount"], paid["signed_off_by"], paid["signed_off_at"]) == (
        1350,
        "Previous senior handler",
        previous,
    )


def test_sign_off_needs_a_claim_waiting_for_it(client):
    assert sign_off(client, "CLM-1001", "HND-01").status_code == 409
    start(client, "CLM-1001")
    decide(client, "CLM-1001", "approve", 420.0)
    assert sign_off(client, "CLM-1001", "HND-01").status_code == 409


def test_sign_off_needs_a_handler_who_can_approve(client, agent_calls):
    with engine().begin() as db:
        db.execute(update(handlers).values(can_approve_over_limit=False))
    start(client, "CLM-1002")
    decide(client, "CLM-1002", "approve", 1350.0)
    response = sign_off(client, "CLM-1002", "HND-02")
    assert response.status_code == 403
    assert "senior handler" in response.json()["detail"]
    assert agent_calls == []


@pytest.mark.parametrize("decision", ["decline", "refer"])
def test_declines_and_referrals_are_decided_and_unpaid(client, decision):
    start(client, "CLM-1003")
    claim = decide(client, "CLM-1003", decision, 0).json()
    assert claim["status"] == "decided"
    assert claim["decision"]["decision"] == decision
    assert pay(client, "CLM-1003", 10.0).status_code == 409


def test_pay_needs_an_approval(client):
    start(client, "CLM-1001")
    assert pay(client, "CLM-1001", 420.0).status_code == 409


def test_list_handlers(client):
    handlers = client.get("/api/handlers").json()
    assert [h["name"] for h in handlers] == [
        "Dana Whitfield",
        "Marcus Oyelaran",
        "Priya Raman",
    ]
    assert [h["can_approve_over_limit"] for h in handlers] == [True, False, False]
