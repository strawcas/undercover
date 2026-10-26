import inspect

import pytest
from fastapi.routing import APIRoute

from backend.tests.conftest import begin_decision_claim, decision_payload


def test_read_apis(client):
    queue = client.get("/api/claims").json()
    assert len(queue) == 25
    assert {row["status"] for row in queue} == {"waiting"}
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["policy"]["plan"] == "premium"
    assert len(claim["items"]) == 3
    assert len(claim["documents"]) == 4
    assert claim["customer"]["previous_claims"]
    assert claim["plan"]["trip_delay_max"] == "800.00"
    assert client.get("/api/company").json()["payout_approval_limit"] == "1000.00"
    assert "repeat_claim_review" not in client.get("/api/company").json()
    assert len(client.get("/api/handlers").json()) == 3
    assert client.get("/api/policy-wording").json()["version"] == "2026.1"
    assert "two or" in client.get("/api/agent-instructions").json()["text"]
    assert (
        client.put("/api/agent-instructions", json={"text": "changed"}).status_code
        == 405
    )


def test_required_openapi_inputs(client):
    operations = []
    for route in client.app.routes:
        if isinstance(route, APIRoute):
            assert route.operation_id
            operations.append(route.operation_id)
            doc = inspect.getdoc(route.endpoint)
            assert doc
            assert len(doc.splitlines()) == 1
    assert len(operations) == len(set(operations))
    schema = client.get("/openapi.json").json()
    for path in schema["paths"].values():
        for operation in path.values():
            assert all(p["required"] for p in operation.get("parameters", []))
            if "requestBody" in operation:
                assert operation["requestBody"]["required"]
    for name in ("DecisionInput", "EventInput", "PaymentInput", "SignOffInput"):
        model = schema["components"]["schemas"][name]
        assert set(model["properties"]) == set(model["required"])


@pytest.mark.parametrize("path", ["", "/events", "/pay", "/decision", "/sign-off"])
def test_missing_claim(client, path):
    if path in {"", "/events"}:
        response = client.get(f"/api/claims/missing{path}")
    else:
        bodies = {
            "/pay": {"amount": "1"},
            "/decision": decision_payload("thread"),
            "/sign-off": {"handler_id": "HND-01"},
        }
        response = client.post(f"/api/claims/missing{path}", json=bodies[path])
    assert response.status_code == 404


def test_decision_and_events(client, repository):
    thread = begin_decision_claim(repository)
    event = {
        "thread_id": thread,
        "action": "Read policy",
        "status": "completed",
        "detail": "Read version 2026.1.",
    }
    assert client.post("/api/claims/CLM-1001/events", json=event).status_code == 201
    assert (
        client.post(
            "/api/claims/CLM-1001/decision", json=decision_payload(thread)
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/claims/CLM-1001/decision", json=decision_payload(thread)
        ).status_code
        == 409
    )
    events = client.get("/api/claims/CLM-1001/events").json()
    assert [e["id"] for e in events] == sorted(e["id"] for e in events)
    assert {e["thread_id"] for e in events} == {thread}
    assert client.get("/api/claims").json()[0]["payment_status"] == "blocked"
    assert client.post("/api/claims/CLM-1002/events", json=event).status_code == 409
    assert (
        client.post(
            "/api/claims/CLM-1002/decision", json=decision_payload(thread)
        ).status_code
        == 409
    )
    assert client.post("/api/claims/missing/events", json=event).status_code == 404


@pytest.mark.parametrize("outcome", ["decline", "refer"])
def test_non_approval(client, repository, outcome):
    thread = begin_decision_claim(repository)
    response = client.post(
        "/api/claims/CLM-1001/decision", json=decision_payload(thread, outcome, "0")
    )
    assert response.status_code == 201
    assert client.get("/api/claims/CLM-1001").json()["status"] == "decided"
    assert client.get("/api/claims").json()[0]["payment_status"] == "unavailable"
    assert (
        client.post("/api/claims/CLM-1001/pay", json={"amount": "1"}).status_code == 409
    )


@pytest.mark.parametrize(
    "change",
    [
        {"payout": "-1"},
        {"payout": "1.001"},
        {"outcome": "unknown"},
        {"outcome": "refer"},
        {"payout": "0"},
        {"policy_sections": []},
        {"policy_sections": ["99"]},
        {"explanation": "Only one sentence is included here."},
    ],
)
def test_invalid_decision(client, change):
    assert (
        client.post(
            "/api/claims/CLM-1001/decision", json=decision_payload("thread") | change
        ).status_code
        == 422
    )
