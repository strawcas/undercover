from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from agent import queue as queue_agent
from agent.tests.stubs import api_client
from backend.claims import Claims
from backend.config import database_identity
from backend.errors import ClaimError
from backend.main import create_app
from backend.models import DecisionInput
from backend.ownership import DatabaseOwner
from backend.tests.conftest import decide_fixture, decision_payload
from backend.worker import execute


def connected_client(app, monkeypatch):
    api = TestClient(app)
    monkeypatch.setattr(queue_agent, "backend", lambda: api_client(api))
    return api


def test_explicit_snapshot_finishes_and_backend_restarts_stay_idle(
    repository, monkeypatch
):
    claims = Claims(repository)
    initial_ids = [row["claim_id"] for row in claims.queue()]
    original = repository.record("claim", "CLM-1001")
    started = []

    def decide(claim_id, thread_id):
        started.append(claim_id)
        active = [row for row in claims.queue() if row["run_active"]]
        assert len(active) == 1
        assert active[0]["claim_id"] == claim_id
        if claim_id == initial_ids[0]:
            repository.add_claim(original | {"claim_id": "CLM-0001"})
        decide_fixture(
            claims, claim_id, DecisionInput.model_validate(decision_payload(thread_id))
        )
        return "Complete"

    app = create_app(repository)
    with connected_client(app, monkeypatch) as client:
        assert not started
        assert client.get("/api/health").headers["X-UnderCover-Instance"]
        assert client.post("/api/claims/CLM-1001/runs").status_code == 404
        assert (
            "/api/claims/{claim_id}/runs"
            not in client.get("/openapi.json").json()["paths"]
        )
        repository.add_claim(original | {"claim_id": "CLM-0000"})
        assert client.get("/api/agent-runs/current").json() == {"run": None}
        queue_agent.run_queue({"mode": "all"}, decide)
        assert started == initial_ids + ["CLM-0000"]
        repository.add_claim(original | {"claim_id": "CLM-0002"})
        for claim_id in ("CLM-0001", "CLM-0002"):
            assert client.get(f"/api/claims/{claim_id}").json()["status"] == "waiting"
            assert claims.events(claim_id) == []
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/claims").status_code == 200
        assert (
            client.post(
                "/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"}
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/claims/CLM-1001/pay", json={"amount": "1250.00"}
            ).status_code
            == 201
        )
        with (
            pytest.raises(RuntimeError, match="already in use"),
            DatabaseOwner(repository.path),
        ):
            pass
    original_events = claims.events("CLM-1001")
    restarted = create_app(repository)
    with connected_client(restarted, monkeypatch):
        assert started == initial_ids + ["CLM-0000"]
        queue_agent.run_queue({"mode": "all"}, decide)
    assert started == initial_ids + ["CLM-0000", "CLM-0001", "CLM-0002"]
    assert claims.events("CLM-1001") == original_events
    with DatabaseOwner(repository.path):
        pass


def test_explicit_empty_queue_records_zero_work_and_later_arrivals_wait(
    repository,
    monkeypatch,
):
    with repository.transaction() as connection:
        connection.execute("UPDATE claims SET state='decided', decision='{}'")
    attempted = []
    app = create_app(repository)
    with connected_client(app, monkeypatch) as api:
        assert api.get("/api/agent-runs/current").json() == {"run": None}
        result = queue_agent.run_queue(
            {"mode": "all"}, lambda c, _t: attempted.append(c) or "Complete"
        )
        assert result["counts"]["worked"] == 0
        repository.add_claim(
            repository.record("claim", "CLM-1001") | {"claim_id": "TEST-LATER"}
        )
        assert not attempted
        assert Claims(repository).detail("TEST-LATER")["status"] == "waiting"


def test_explicit_failure_is_retried_within_the_same_run(repository, monkeypatch):
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE claims SET state='decided', decision='{}' WHERE queue_position > 2"
        )
    claims = Claims(repository)
    attempted = []

    def decide(claim_id, thread_id):
        attempted.append(claim_id)
        if claim_id == "CLM-1001" and attempted.count(claim_id) == 1:
            raise RuntimeError("Synthetic provider failure")
        decide_fixture(
            claims, claim_id, DecisionInput.model_validate(decision_payload(thread_id))
        )
        return "Complete"

    app = create_app(repository)
    with connected_client(app, monkeypatch):
        queue_agent.run_queue({"mode": "all"}, decide)
        assert attempted == ["CLM-1001", "CLM-1002", "CLM-1001"]
        assert claims.detail("CLM-1001")["status"] == "awaiting_sign_off"
    restarted = create_app(repository)
    with connected_client(restarted, monkeypatch):
        queue_agent.run_queue({"mode": "all"}, decide)
        assert attempted == ["CLM-1001", "CLM-1002", "CLM-1001"]
        assert claims.detail("CLM-1001")["decision"] is not None


def test_backend_startup_and_refresh_start_nothing(repository, monkeypatch):
    started = []
    monkeypatch.setattr(queue_agent, "model", lambda: started.append("model"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("UNDERCOVER_MAX_CLAIMS", "invalid")
    for _ in range(2):
        app = create_app(repository)
        with connected_client(app, monkeypatch) as api:
            assert not hasattr(app.state, "worker")
            assert api.get("/api/health").json() == {"status": "ok"}
            assert api.get("/api/agent-runs/current").json() == {"run": None}
            assert {row["status"] for row in api.get("/api/claims").json()} == {
                "waiting"
            }
            assert api.get("/api/claims/CLM-1001/events").json() == []
    assert not started


def test_slow_shutdown_fences_the_attempt_before_releasing_ownership(
    repository, monkeypatch
):
    entered = Event()
    finish = Event()
    late = Event()
    claims = Claims(repository)

    def slow_runner(claim_id, thread_id):
        entered.set()
        assert finish.wait(15)
        with pytest.raises(ClaimError, match="no longer active"):
            decide_fixture(
                claims,
                claim_id,
                DecisionInput.model_validate(decision_payload(thread_id)),
            )
        late.set()
        return "Complete"

    app = create_app(repository)
    results = []
    command = Thread(
        target=lambda: results.append(
            queue_agent.run_queue({"mode": "limited", "max_claims": 1}, slow_runner)
        )
    )
    try:
        with connected_client(app, monkeypatch):
            command.start()
            assert entered.wait(5)
        assert claims.detail("CLM-1001")["run_active"] is False
    finally:
        finish.set()
        assert late.wait(5)
        command.join(timeout=5)
    assert not command.is_alive()
    assert results[0]["status"] == "interrupted"
    assert claims.detail("CLM-1001")["decision"] is None
    assert claims.events("CLM-1002") == []
    with DatabaseOwner(repository.path):
        pass


def test_failed_claim_does_not_block_the_next(repository, client, monkeypatch):
    claims = Claims(repository)
    started = []

    def decide(claim_id, thread_id):
        started.append(claim_id)
        if claim_id == "CLM-1001":
            raise RuntimeError("Synthetic failure")
        decide_fixture(
            claims, claim_id, DecisionInput.model_validate(decision_payload(thread_id))
        )
        return "Complete"

    monkeypatch.setattr(queue_agent, "backend", lambda: api_client(client))
    result = queue_agent.run_queue({"mode": "limited", "max_claims": 2}, decide)
    assert started == ["CLM-1001", "CLM-1002", "CLM-1001"]
    assert claims.detail("CLM-1001")["status"] == "handler_review"
    assert claims.detail("CLM-1002")["status"] == "awaiting_sign_off"
    assert result["counts"]["worked"] == 2
    assert result["counts"]["assessment_attempts"] == 3


def test_keyboard_interrupt_records_a_recoverable_failure(repository):
    claims = Claims(repository)
    thread = claims.start("CLM-1001")

    def interrupt(_claim, _thread):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute(claims, "CLM-1001", thread, interrupt)
    assert claims.detail("CLM-1001")["status"] == "waiting"
    assert claims.events("CLM-1001")[-1]["status"] == "failed"


def test_health_identifies_the_exact_backend_database(repository, client, tmp_path):
    response = client.get("/api/health")
    assert response.headers["X-UnderCover-Instance"]
    assert response.headers["X-UnderCover-Database"] == database_identity(
        repository.path
    )
    assert response.headers["X-UnderCover-Database"] != database_identity(
        tmp_path / "another.db"
    )


def test_only_an_explicit_capped_run_admits_claims(repository, monkeypatch):
    monkeypatch.setenv("UNDERCOVER_MAX_CLAIMS", "5")
    claims = Claims(repository)
    started = []

    def decide(claim_id, thread_id):
        started.append(claim_id)
        decide_fixture(
            claims, claim_id, DecisionInput.model_validate(decision_payload(thread_id))
        )
        return "Complete"

    app = create_app(repository)
    with connected_client(app, monkeypatch) as api:
        assert not started
        assert api.get("/api/agent-runs/current").json() == {"run": None}
        queue_agent.run_queue({"mode": "limited", "max_claims": 5}, decide)
        assert len(set(started)) == len(started) == 5
        run = api.get("/api/agent-runs/current").json()["run"]
        assert run["scope"] == {"mode": "limited", "max_claims": 5}
        assert run["counts"]["worked"] == 5
        assert sum(row["status"] == "waiting" for row in claims.queue()) == 20
