"""The backend forwards starts before writing any run state."""

import json

import httpx
import pytest


@pytest.mark.parametrize("n", [1, 25])
def test_dispatch_forwarding(client, agent_answers, n):
    requests = []

    def accept(request):
        requests.append((request.url.path, json.loads(request.content)))
        assert request.headers["Authorization"] == "Bearer offline-test-agent-key"
        return httpx.Response(200, json={"accepted": True})

    agent_answers(accept)
    assert client.post("/api/dispatches", json={"concurrency": n}).status_code == 202
    assert requests == [("/invocations", {"operation": "start_dispatch", "agents": n})]
    assert client.get("/api/runs/latest").json() is None


def test_busy_and_unavailable_create_nothing(client, agent_answers):
    agent_answers(
        lambda request: httpx.Response(200, json={"refused": "Already running"})
    )
    assert client.post("/api/dispatches", json={"concurrency": 3}).status_code == 409

    def offline(request):
        raise httpx.ConnectError("offline")

    agent_answers(offline)
    response = client.post("/api/dispatches", json={"concurrency": 3})
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]
    assert client.get("/api/runs/latest").json() is None
    agent_answers(lambda request: httpx.Response(500, text="Internal Server Error"))
    assert client.post("/api/dispatches", json={"concurrency": 3}).status_code == 503


@pytest.mark.parametrize("n", [0, 26])
def test_backend_concurrency_limits(client, n):
    assert client.post("/api/dispatches", json={"concurrency": n}).status_code == 422


def test_evaluation_forwarding_is_state_free(client, agent_answers):
    calls = []

    def accept(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"accepted": True})

    agent_answers(accept)
    body = {"set_name": "held_out", "tiny": True, "concurrency": 5}
    assert client.post("/api/evaluations", json=body).status_code == 202
    assert calls == [
        (
            "/invocations",
            {
                "operation": "start_evaluation",
                "set_name": "held_out",
                "tiny": True,
                "agents": 5,
            },
        )
    ]
    assert client.get("/api/evaluations").json() == []
