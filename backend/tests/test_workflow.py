import httpx
import pytest
from sqlalchemy import select, update

from backend.database import decisions, engine, handlers
from backend.tests.claim_requests import decide, pay, sign_off, start


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
            "/api/claims/CLM-1001/steps", json={"kind": "message", "content": "Hi"}
        ).status_code
        == 409
    )
    start(client, "CLM-1001")
    for kind, content in (("tool_call", "get_claim"), ("tool_result", "{}")):
        response = client.post(
            "/api/claims/CLM-1001/steps", json={"kind": kind, "content": content}
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
        "/api/claims/CLM-1001/steps", json={"kind": "thought", "content": "Hmm"}
    )
    assert response.status_code == 422


def test_decision_needs_a_claim_in_progress(client):
    assert decide(client, "CLM-1001", "decline", 0).status_code == 409
    assert decide(client, "CLM-9999", "decline", 0).status_code == 404


@pytest.mark.parametrize(
    ("decision", "payout", "section"),
    [
        ("approve", 0, "Section 3"),
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
        json={
            "decision": decision,
            "payout": payout,
            "section": section,
            "reason": "Because.",
        },
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


def test_a_sign_off_is_recorded_and_the_agent_is_told(client, agent_calls):
    start(client, "CLM-1002")
    decide(client, "CLM-1002", "approve", 1350.0)
    assert sign_off(client, "CLM-1002", "HND-99").status_code == 403
    claim = sign_off(client, "CLM-1002", "HND-01").json()
    assert (claim["status"], claim["payment"]) == ("in_progress", None)
    assert claim["decision"]["signed_off_by"] == "Dana Whitfield"
    signed_off_at = claim["decision"]["signed_off_at"]
    assert signed_off_at
    assert agent_calls == ["/api/claims/CLM-1002/resume"]

    assert pay(client, "CLM-1002", 1300.0).status_code == 422
    paid = pay(client, "CLM-1002", 1350.0).json()
    assert paid["status"] == "decided"
    assert paid["payment"]["amount"] == 1350.0
    assert (paid["payment"]["signed_off_by"], paid["payment"]["signed_off_at"]) == (
        "Dana Whitfield",
        signed_off_at,
    )
    assert sign_off(client, "CLM-1002", "HND-01").status_code == 409
    assert agent_calls == ["/api/claims/CLM-1002/resume"]


def test_the_agent_is_told_only_once_the_sign_off_is_saved(client, agent_answers):
    seen = []

    def read_the_sign_off(request):
        with engine().connect() as db:
            query = select(decisions.c.signed_off_by).where(
                decisions.c.claim_id == "CLM-1002"
            )
            seen.append(db.execute(query).scalar_one())
        return httpx.Response(202, json={"status": "accepted"})

    agent_answers(read_the_sign_off)
    start(client, "CLM-1002")
    decide(client, "CLM-1002", "approve", 1350.0)
    sign_off(client, "CLM-1002", "HND-01")
    assert seen == ["Dana Whitfield"]


def test_a_sign_off_stands_when_the_agent_cant_be_reached(
    client, agent_answers, caplog
):
    def unreachable(request):
        raise httpx.ConnectError("Connection refused")

    agent_answers(unreachable)
    start(client, "CLM-1002")
    decide(client, "CLM-1002", "approve", 1350.0)
    response = sign_off(client, "CLM-1002", "HND-01")
    assert response.status_code == 200
    claim = client.get("/api/claims/CLM-1002").json()
    assert claim["status"] == "in_progress"
    assert claim["decision"]["signed_off_by"] == "Dana Whitfield"
    assert [(r.levelname, r.message) for r in caplog.records] == [
        (
            "WARNING",
            "The agent didn't pick up CLM-1002 after its sign-off: Connection refused",
        )
    ]


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
