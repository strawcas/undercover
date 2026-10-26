from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.claims import Claims
from backend.main import create_app
from backend.models import DecisionInput
from backend.ownership import DatabaseOwner
from backend.tests.conftest import decide_fixture, decision_payload
from backend.worker import QueueWorker, api_ready, execute


def test_startup_snapshot_finishes_and_later_arrivals_wait(repository):
    claims = Claims(repository)
    gate = Event()
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

    app = create_app(repository, decide, worker_ready=gate.is_set)
    with TestClient(app) as client:
        assert not started
        assert client.get("/api/health").headers["X-UnderCover-Instance"]
        assert client.post("/api/claims/CLM-1001/runs").status_code == 404
        assert (
            "/api/claims/{claim_id}/runs"
            not in client.get("/openapi.json").json()["paths"]
        )
        repository.add_claim(original | {"claim_id": "CLM-0000"})
        gate.set()
        app.state.worker.thread.join(timeout=15)
        assert not app.state.worker.thread.is_alive()
        assert started == initial_ids
        repository.add_claim(original | {"claim_id": "CLM-0002"})
        for claim_id in ("CLM-0000", "CLM-0001", "CLM-0002"):
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
    assert not app.state.worker.thread.is_alive()
    original_events = claims.events("CLM-1001")
    restarted = create_app(repository, decide, worker_ready=lambda: True)
    with TestClient(restarted):
        restarted.state.worker.thread.join(timeout=15)
        assert not restarted.state.worker.thread.is_alive()
    assert started == initial_ids + ["CLM-0000", "CLM-0001", "CLM-0002"]
    assert claims.events("CLM-1001") == original_events
    with DatabaseOwner(repository.path):
        pass


def test_empty_startup_queue_finishes_without_waiting_for_api_or_later_arrivals(
    repository,
):
    with repository.transaction() as connection:
        connection.execute("UPDATE claims SET state='decided', decision='{}'")
    attempted = []
    ready = Event()
    app = create_app(
        repository,
        lambda c, _t: attempted.append(c) or "Complete",
        worker_ready=ready.is_set,
    )
    with TestClient(app):
        app.state.worker.thread.join(timeout=5)
        assert not app.state.worker.thread.is_alive()
        repository.add_claim(
            repository.record("claim", "CLM-1001") | {"claim_id": "TEST-LATER"}
        )
        assert not attempted
        assert Claims(repository).detail("TEST-LATER")["status"] == "waiting"


def test_startup_failure_is_retried_only_on_a_later_start(repository):
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

    app = create_app(repository, decide, worker_ready=lambda: True)
    with TestClient(app):
        app.state.worker.thread.join(timeout=10)
        assert not app.state.worker.thread.is_alive()
        assert attempted == ["CLM-1001", "CLM-1002"]
        assert claims.detail("CLM-1001")["status"] == "waiting"
    restarted = create_app(repository, decide, worker_ready=lambda: True)
    with TestClient(restarted):
        restarted.state.worker.thread.join(timeout=10)
        assert not restarted.state.worker.thread.is_alive()
        assert attempted == ["CLM-1001", "CLM-1002", "CLM-1001"]
        assert claims.detail("CLM-1001")["decision"] is not None


def test_shutdown_before_readiness_starts_nothing(repository):
    started = []
    app = create_app(
        repository,
        lambda c, _t: started.append(c) or "Complete",
        worker_ready=lambda: False,
    )
    with TestClient(app):
        pass
    assert not started
    assert not app.state.worker.thread.is_alive()


def test_slow_shutdown_keeps_ownership_until_the_attempt_finishes(repository):
    entered = Event()
    finish = Event()
    claims = Claims(repository)

    def slow_runner(claim_id, thread_id):
        entered.set()
        assert finish.wait(15)
        decide_fixture(
            claims, claim_id, DecisionInput.model_validate(decision_payload(thread_id))
        )
        return "Complete"

    app = create_app(repository, slow_runner, worker_ready=lambda: True)
    try:
        with TestClient(app):
            assert entered.wait(5)
        assert app.state.worker.thread.is_alive()
        with (
            pytest.raises(RuntimeError, match="already in use"),
            DatabaseOwner(repository.path),
        ):
            pass
    finally:
        finish.set()
        app.state.worker.thread.join(timeout=5)
    assert not app.state.worker.thread.is_alive()
    assert claims.detail("CLM-1001")["decision"] is not None
    assert claims.events("CLM-1002") == []
    with DatabaseOwner(repository.path):
        pass


def test_failed_claim_does_not_block_the_next(repository):
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

    worker = QueueWorker(claims, decide, lambda: True, lambda: None)
    for _ in range(3):
        worker.step({"CLM-1001", "CLM-1002"})
    assert started == ["CLM-1001", "CLM-1002"]
    assert claims.detail("CLM-1001")["status"] == "waiting"
    assert claims.detail("CLM-1002")["status"] == "awaiting_sign_off"
    worker.stopping.set()
    assert worker.step() is None


def test_keyboard_interrupt_records_a_recoverable_failure(repository):
    claims = Claims(repository)
    thread = claims.start("CLM-1001")

    def interrupt(_claim, _thread):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute(claims, "CLM-1001", thread, interrupt)
    assert claims.detail("CLM-1001")["status"] == "waiting"
    assert claims.events("CLM-1001")[-1]["status"] == "failed"


def test_readiness_uses_the_exact_backend_instance(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise httpx.ConnectError("Not listening")

    monkeypatch.setattr(httpx, "get", unavailable)
    assert not api_ready("http://localhost:8000", "expected")
    monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: httpx.Response(503))
    assert not api_ready("http://localhost:8000", "expected")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *_a, **_kw: httpx.Response(
            200, headers={"X-UnderCover-Instance": "expected"}
        ),
    )
    assert api_ready("http://localhost:8000", "expected")
    assert not api_ready("http://localhost:8000", "another")
