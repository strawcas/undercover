"""The app owns admission, committed work and compensation."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from sqlalchemy import Connection
from sqlalchemy.dialects import postgresql

from backend import improvements, runs, work_admission
from backend.tests.improvement_support import new_round, proposal
from backend.tests.run_requests import configuration, start

DISPATCH = {"concurrency": 25}
EVALUATION = {"set_name": "evaluation", "tiny": True, "concurrency": 5}
STARTS = "2026-11-10T09:00:00+00:00"


@pytest.mark.parametrize("n", [0, 26])
def test_backend_concurrency_limits(client, n):
    assert client.post("/api/dispatches", json={"concurrency": n}).status_code == 422


def test_service_has_no_standalone_admission_bypass(client):
    assert client.post("/api/runs", json=configuration()).status_code == 422
    assert client.post("/api/improver/rounds", json={}).status_code == 404
    assert (
        client.post("/api/improver/rounds/r/proposals/p/decision", json={}).status_code
        == 404
    )


def test_nested_evaluation_requires_an_active_worker_and_free_child_slot(client):
    current = new_round(client)
    identity = current["round_id"]
    internal = f"/api/improver/rounds/{identity}"
    selection = configuration(
        kind="evaluation", round_id=identity, set_name="evaluation", tiny=False
    )
    route = "/api/runs"
    assert client.post(route, json=selection).status_code == 409
    client.post(f"{internal}/take", json={"proposal_id": None}).raise_for_status()
    assert client.post(route, json=selection | {"tiny": True}).status_code == 422
    run_id = client.post(route, json=selection).raise_for_status().json()["run_id"]
    assert client.post(route, json=selection).status_code == 409
    client.post(f"/api/runs/{run_id}/finish").raise_for_status()
    client.post(
        f"{internal}/proposals", json=proposal(client, current, kind="recipe")
    ).raise_for_status()
    client.post("/api/dispatches", json=DISPATCH).raise_for_status()
    assert client.post(route, json=selection).status_code == 409


def test_dispatch_commits_before_invocation(client, agent_answers):
    calls = []

    def accept(request):
        body = json.loads(request.content)
        assert request.url.path == "/invocations"
        assert request.headers["Authorization"] == "Bearer offline-test-agent-key"
        assert body["operation"] == "work_run"
        stored = client.get(f"/api/runs/{body['run_id']}").raise_for_status().json()
        assert stored["status"] == "running"
        assert stored["concurrency"] == DISPATCH["concurrency"]
        calls.append(body["run_id"])
        return httpx.Response(200, json={"accepted": True})

    agent_answers(accept)
    started = client.post("/api/dispatches", json=DISPATCH)
    assert started.status_code == 202
    assert calls == [started.json()["run_id"]]


@pytest.mark.parametrize(
    ("path", "selection", "owner"),
    [
        ("/api/dispatches", DISPATCH, "dispatch"),
        ("/api/evaluations", EVALUATION, "evaluation"),
        ("/api/rounds", {}, "improvement round"),
    ],
)
def test_one_slot_across_all_work_kinds(client, path, selection, owner):
    assert client.post(path, json=selection).status_code == 202
    denied = client.post("/api/dispatches", json=DISPATCH)
    assert denied.status_code == 409
    assert owner in denied.json()["detail"]


def test_two_admissions_on_separate_sqlite_connections_have_one_winner(client):
    with ThreadPoolExecutor(max_workers=2) as workers:
        replies = list(
            workers.map(
                lambda _: client.post("/api/dispatches", json=DISPATCH), range(2)
            )
        )
    assert sorted(reply.status_code for reply in replies) == [202, 409]


def test_admission_takes_a_postgresql_row_lock_with_an_update():
    statements = []

    def execute(statement):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return SimpleNamespace(rowcount=1)

    work_admission.lock(cast(Connection, SimpleNamespace(execute=execute)))
    assert len(statements) == 1
    assert statements[0].startswith(
        "UPDATE work_gate SET id=work_gate.id WHERE work_gate.id"
    )


def test_admission_uses_the_last_signal_and_rejects_a_late_heartbeat(
    client, monkeypatch
):
    clock = [STARTS]
    monkeypatch.setattr(work_admission, "now", lambda: clock[0])
    monkeypatch.setattr(runs, "now", lambda: clock[0])
    run_id = (
        client.post("/api/dispatches", json=DISPATCH)
        .raise_for_status()
        .json()["run_id"]
    )
    base = f"/api/runs/{run_id}"
    client.post(f"{base}/take").raise_for_status()
    clock[0] = "2026-11-10T09:04:59+00:00"
    client.put(f"{base}/heartbeat").raise_for_status()
    clock[0] = "2026-11-10T09:09:58+00:00"
    assert client.post("/api/evaluations", json=EVALUATION).status_code == 409
    clock[0] = "2026-11-10T09:09:59+00:00"
    assert client.get("/api/runs/active").json() is None
    assert client.get(base).json()["status"] == "running"
    assert client.put(f"{base}/heartbeat").status_code == 409
    assert client.post(f"{base}/take").status_code == 409
    assert client.post("/api/evaluations", json=EVALUATION).status_code == 202
    assert client.put(f"{base}/heartbeat").status_code == 409
    previous = client.get(base).json()
    assert previous["status"] == "stopped"
    assert previous["stop_reason"] == "The agent stopped answering"


@pytest.mark.parametrize("kind", ["dispatch", "round"])
@pytest.mark.parametrize("certainty", ["definite", "uncertain", "unconfirmed-take"])
def test_start_is_compensated_only_after_definite_rejection(
    client, agent_answers, kind, certainty
):
    def answer(request):
        if certainty == "uncertain":
            raise httpx.ReadTimeout("no reply", request=request)
        if certainty == "unconfirmed-take":
            return httpx.Response(200, json={"uncertain": True})
        return httpx.Response(200, json={"refused": "No worker is available"})

    agent_answers(answer)
    path = "/api/dispatches" if kind == "dispatch" else "/api/rounds"
    response = client.post(path, json=DISPATCH if kind == "dispatch" else {})
    assert response.status_code == (409 if certainty == "definite" else 503)
    record = (
        client.get("/api/runs/latest").json()
        if kind == "dispatch"
        else client.get("/api/rounds").json()[0]
    )
    if certainty == "definite":
        assert (record["status"], record["stop_reason"]) == (
            "stopped",
            "No worker is available",
        )
    else:
        assert record["status"] == ("running" if kind == "dispatch" else "baseline")
        assert "Reload" in response.json()["detail"]


def test_recipe_review_is_recorded_before_invocation_and_reversible(
    client, agent_answers
):
    current = new_round(client)
    identity = current["round_id"]
    pending = proposal(client, current, kind="recipe")
    root = f"/api/improver/rounds/{identity}"
    client.post(f"{root}/proposals", json=pending).raise_for_status()
    original = client.get("/api/recipes").json()

    def refuse(request):
        assert json.loads(request.content) == {
            "operation": "carry_round_on",
            "round_id": identity,
            "proposal_id": pending["proposal_id"],
        }
        stored = client.get(root).json()
        assert stored["status"] == "working"
        assert stored["pending_proposal_id"] == pending["proposal_id"]
        assert stored["proposals"][0]["decision"] is None
        assert client.get("/api/recipes").json() == original
        return httpx.Response(200, json={"refused": "Worker unavailable"})

    agent_answers(refuse)
    review = f"/api/rounds/{identity}/proposals/{pending['proposal_id']}/decision"
    assert (
        client.post(review, json={"decision": "approve", "reason": None}).status_code
        == 409
    )
    assert client.get(root).json()["status"] == "awaiting_recipe"
    assert client.get("/api/recipes").json() == original


def test_uncertain_review_uses_a_fresh_lease_and_applies_once_on_take(
    client, agent_answers, monkeypatch
):
    clock = [STARTS]
    monkeypatch.setattr(work_admission, "now", lambda: clock[0])
    monkeypatch.setattr(improvements, "now", lambda: clock[0])
    current = new_round(client)
    identity = current["round_id"]
    pending = proposal(client, current, kind="recipe")
    root = f"/api/improver/rounds/{identity}"
    client.post(f"{root}/proposals", json=pending).raise_for_status()
    original = client.get("/api/recipes").json()

    def timeout(request):
        raise httpx.ReadTimeout("no reply", request=request)

    agent_answers(timeout)
    clock[0] = "2026-11-10T09:40:00+00:00"
    proposal_id = pending["proposal_id"]
    submitted = client.post(
        f"/api/rounds/{identity}/proposals/{proposal_id}/decision",
        json={"decision": "approve", "reason": None},
    )
    assert submitted.status_code == 503
    assert client.get(root).json()["status"] == "working"
    assert client.get("/api/recipes").json() == original
    assert client.post("/api/dispatches", json=DISPATCH).status_code == 409
    clock[0] = "2026-11-10T09:44:59+00:00"
    taken = client.post(f"{root}/take", json={"proposal_id": proposal_id})
    assert taken.raise_for_status().json()["review"] == {"type": "approve"}
    assert (
        client.post(f"{root}/take", json={"proposal_id": proposal_id}).status_code
        == 409
    )
    assert len(client.get("/api/recipes").json()) == len(original) + 1


def test_round_expiry_stops_its_evaluation(client, monkeypatch):
    clock = [STARTS]
    monkeypatch.setattr(work_admission, "now", lambda: clock[0])
    monkeypatch.setattr(improvements, "now", lambda: clock[0])
    current = new_round(client)
    identity = current["round_id"]
    client.post(
        f"/api/improver/rounds/{identity}/take", json={"proposal_id": None}
    ).raise_for_status()
    nested = (
        start(
            client,
            kind="evaluation",
            round_id=identity,
            set_name="evaluation",
            tiny=False,
        )
        .raise_for_status()
        .json()
    )
    run_id = nested["run_id"]
    claim_id = nested["members"][0]["claim_id"]
    claim = f"/api/claims/{claim_id}"
    client.post(
        f"{claim}/start", json={"thread_id": "evaluation-attempt", "run_id": run_id}
    ).raise_for_status()
    client.put(
        f"{claim}/work-slices/in-flight", json={"run_id": run_id, "seconds": 1}
    ).raise_for_status()
    before = client.get(f"/api/runs/{run_id}").json()["members"][0]
    clock[0] = "2026-11-10T09:05:00+00:00"
    assert client.get("/api/rounds").json()[0]["work_expired"]
    assert client.post("/api/dispatches", json=DISPATCH).status_code == 202
    assert (
        client.get(f"/api/improver/rounds/{identity}").json()["stop_reason"]
        == "The agent stopped answering"
    )
    saved = client.get(f"/api/runs/{nested['run_id']}").json()
    assert (saved["status"], saved["stop_reason"]) == (
        "stopped",
        "The agent stopped answering",
    )
    receipt = {
        "reply_id": "late-billed-reply",
        "thread_id": "evaluation-attempt",
        "run_id": run_id,
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
        "cost": 0.02,
    }
    for _ in range(2):
        client.post(f"{claim}/model-calls", json=receipt).raise_for_status()
    assert client.get(f"/api/runs/{run_id}").json()["llm_cost"] == 0.02
    assert (
        client.post(
            f"{claim}/failure", json={"reason": "late", "run_id": run_id}
        ).status_code
        == 409
    )
    after = client.get(f"/api/runs/{run_id}").json()["members"][0]
    assert after["decision"] == before["decision"]
    assert after["claim_status"] == before["claim_status"]


def test_stopped_dispatch_cannot_write_an_original_claim(client):
    run_id = (
        client.post("/api/dispatches", json=DISPATCH)
        .raise_for_status()
        .json()["run_id"]
    )
    claim = "/api/claims/CLM-1001"
    identity = {"run_id": run_id, "thread_id": "stale-dispatch-thread"}
    client.post(f"{claim}/start", json=identity).raise_for_status()
    client.post(
        f"/api/runs/{run_id}/stop", json={"reason": "Stopped"}
    ).raise_for_status()
    response = client.post(
        f"{claim}/decision",
        json=identity
        | {
            "decision": "decline",
            "payout": 0,
            "reason": "No coverage",
            "section": "3",
            "decided_by": "UnderCover Agent",
        },
    )
    assert response.status_code == 409
    assert client.get(claim).json()["decision"] is None
