import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from agent import harness, improver_api, service
from agent.tests.records import claim_of, steps_of
from agent.tests.stubs import decide, graph_on, stub
from api_rules import rule_breaks
from backend import agent_key
from backend.tests.auth_support import AGENT_HEADERS


@pytest.fixture
def serve(monkeypatch):
    """Run the agent's service, without its lifespan, on a given claim graph."""

    def with_graph(graph):
        monkeypatch.setattr(harness, "execution_graph", lambda version=None: graph)
        return TestClient(service.app, headers=AGENT_HEADERS)

    return with_graph


def resume(agent, claim_id):
    return agent.post(f"/api/claims/{claim_id}/resume")


def test_the_service_follows_the_api_rules():
    assert rule_breaks(service.app.openapi()) == []


def test_health():
    assert TestClient(service.app).get("/api/health").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {agent_key.HEADER: "wrong"}])
def test_round_writes_refuse_direct_unauthenticated_service_calls(
    client, monkeypatch, headers
):
    def unexpected(*args):
        pytest.fail("The service attempted an unauthenticated round write")

    monkeypatch.setattr(improver_api, "start_round", unexpected)
    monkeypatch.setattr(improver_api, "finish", unexpected)
    monkeypatch.setattr(improver_api, "decide", unexpected)
    handler = client.get("/api/me").json()
    routes = TestClient(service.app, headers=headers)
    assert routes.post("/api/rounds", json=handler).status_code == 401
    assert routes.post("/api/rounds/1/stop", json=handler).status_code == 401
    assert (
        routes.post(
            "/api/rounds/1/proposals/1/decision",
            json={"handler": handler, "decision": "approve", "reason": None},
        ).status_code
        == 401
    )


def test_a_sign_off_picks_the_claim_up_and_pays_it_once(
    client, serve, agent_calls, traces
):
    model = stub(*decide("approve", 1350.0))
    graph = graph_on(model, InMemorySaver())
    harness.work_claim("CLM-1002", graph)
    signed = client.post("/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"})
    assert agent_calls == ["/api/claims/CLM-1002/resume"]

    agent = serve(graph)
    response = resume(agent, "CLM-1002")
    assert response.status_code == 202
    claim = claim_of(client, "CLM-1002")
    assert claim["status"] == "decided"
    assert claim["payment"]["amount"] == 1350.0
    assert claim["payment"]["signed_off_by"] == "Dana Whitfield"
    signed_off_at = signed.json()["decision"]["signed_off_at"]
    assert claim["payment"]["signed_off_at"] == signed_off_at
    assert len(model.prompts) == 2
    session = f"CLM-1002:{claim['thread_id']}"
    assert [(t["langfuse_session_id"], t["langfuse_trace_name"]) for t in traces] == [
        (session, "Work claim"),
        (session, "Carry on"),
    ]

    assert resume(agent, "CLM-1002").status_code == 409
    assert claim_of(client, "CLM-1002")["payment"] == claim["payment"]


def test_a_claim_waiting_for_sign_off_is_not_picked_up(client, serve):
    graph = graph_on(stub(*decide("approve", 1350.0)))
    harness.work_claim("CLM-1002", graph)
    before = steps_of(client, "CLM-1002")
    response = resume(serve(graph), "CLM-1002")
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Claim CLM-1002 has no sign-off waiting to be paid"
    )
    assert claim_of(client, "CLM-1002")["status"] == "waiting_for_sign_off"
    assert steps_of(client, "CLM-1002") == before


def test_a_claim_in_progress_without_a_sign_off_is_left_to_its_agent(client, serve):
    client.post("/api/claims/CLM-1001/start", json={"thread_id": "working"})
    response = resume(serve(graph_on(stub())), "CLM-1001")
    assert response.status_code == 409
    claim = claim_of(client, "CLM-1001")
    assert (claim["status"], claim["attempts"]) == ("in_progress", 1)
    assert steps_of(client, "CLM-1001") == []


def test_an_unknown_claim(serve):
    response = resume(serve(graph_on(stub())), "CLM-9999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Claim CLM-9999 not found"


def test_sign_off_is_queued_before_current_worker_releases(client, monkeypatch):
    graph = graph_on(stub(*decide("approve", 1350)))
    monkeypatch.setattr(harness, "execution_graph", lambda version=None: graph)
    harness.work_claim("CLM-1002", graph)
    client.post("/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"})
    assert harness.reserve_claim("CLM-1002")
    background = BackgroundTasks()
    assert service.resume_claim("CLM-1002", background) == {"status": "accepted"}
    with pytest.raises(HTTPException) as duplicate:
        service.resume_claim("CLM-1002", BackgroundTasks())
    assert duplicate.value.status_code == 409
    harness.release_claim("CLM-1002")
    assert harness.execute_claim("CLM-1002", "other-run", 1).startswith("Skipped:")
    task = background.tasks[0]
    task.func(*task.args, **task.kwargs)
    assert not harness.is_reserved("CLM-1002")
    assert client.get("/api/claims/CLM-1002").json()["payment"]["amount"] == 1350
