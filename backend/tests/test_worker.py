from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.claims import Claims
from backend.main import create_app
from backend.models import DecisionInput
from backend.ownership import DatabaseOwner
from backend.tests.conftest import decision_payload
from backend.worker import QueueWorker, api_ready, execute


def test_automatic_lifecycle_readiness_and_arrivals(repository):
    claims = Claims(repository)
    gate = Event()
    drained = Event()
    arrival_done = Event()
    started = []

    def decide(claim_id, thread_id):
        started.append(claim_id)
        active = [row for row in claims.queue() if row["run_active"]]
        assert len(active) == 1
        assert active[0]["claim_id"] == claim_id
        claims.decide(
            claim_id, DecisionInput.model_validate(decision_payload(thread_id))
        )
        if len(started) == 25:
            drained.set()
        if claim_id == "CLM-0000":
            arrival_done.set()
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
        gate.set()
        assert drained.wait(15)
        assert started == [row["claim_id"] for row in claims.queue()]
        repository.add_claim(
            repository.record("claim", "CLM-1001") | {"claim_id": "CLM-0000"}
        )
        assert arrival_done.wait(5)
    assert not app.state.worker.thread.is_alive()
    assert claims.detail("CLM-0000")["decision"] is not None
    with DatabaseOwner(repository.path):
        pass


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


def test_failed_claim_does_not_block_the_next(repository):
    claims = Claims(repository)
    started = []

    def decide(claim_id, thread_id):
        started.append(claim_id)
        if claim_id == "CLM-1001":
            raise RuntimeError("Synthetic failure")
        claims.decide(
            claim_id, DecisionInput.model_validate(decision_payload(thread_id))
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
