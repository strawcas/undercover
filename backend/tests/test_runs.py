import pytest
from fastapi.testclient import TestClient

from backend import worker
from backend.claims import Claims
from backend.main import create_app
from backend.models import DecisionInput
from backend.tests.conftest import decision_payload


def test_start_run_and_completion(repository):
    claims = Claims(repository)

    def runner(claim_id, thread_id):
        assert claims.events(claim_id)[0]["status"] == "started"
        assert claims.detail(claim_id)["run_active"]
        claims.decide(
            claim_id,
            DecisionInput.model_validate(decision_payload(thread_id, payout="420")),
        )
        return "Complete"

    with TestClient(create_app(repository, runner)) as client:
        response = client.post("/api/claims/CLM-1001/runs")
        assert response.status_code == 202
        assert response.json()["thread_id"]
        assert response.json()["status"] == "in_progress"
        detail = client.get("/api/claims/CLM-1001").json()
        assert detail["status"] == "decided"
        assert detail["run_active"] is False
        assert client.post("/api/claims/CLM-1001/runs").status_code == 409
        assert client.post("/api/claims/missing/runs").status_code == 404


def test_failed_run_retry_and_redacted_error(repository):
    def failing(_claim_id, _thread_id):
        raise RuntimeError("secret provider details")

    with TestClient(create_app(repository, failing)) as client:
        first = client.post("/api/claims/CLM-1001/runs").json()
        detail = client.get("/api/claims/CLM-1001").json()
        assert detail["status"] == "waiting"
        assert detail["decision"] is None
        events = client.get("/api/claims/CLM-1001/events").json()
        assert events[-1]["status"] == "failed"
        assert "secret" not in str(events)
        second = client.post("/api/claims/CLM-1001/runs").json()
        assert first["thread_id"] != second["thread_id"]


def test_finished_without_decision(repository):
    claims = Claims(repository)
    thread = claims.start("CLM-1001")
    worker.execute(claims, "CLM-1001", thread, lambda _c, _t: "No decision")
    assert claims.detail("CLM-1001")["status"] == "waiting"
    assert "No decision" in claims.events("CLM-1001")[-1]["detail"]


def test_stale_completion_cannot_end_new_run(repository):
    claims = Claims(repository)
    thread = claims.start("CLM-1001")
    claims.finish("CLM-1001", thread, "Failed")
    new_thread = claims.start("CLM-1001")
    claims.finish("CLM-1001", thread)
    assert claims.detail("CLM-1001")["run_active"]
    claims.finish("CLM-1001", new_thread)
    claims.finish("CLM-1001", new_thread)


@pytest.mark.parametrize("committed", [False, True])
def test_restart_recovers_interrupted_work(repository, committed):
    claims = Claims(repository)
    thread = claims.start("CLM-1001")
    if committed:
        claims.decide(
            "CLM-1001", DecisionInput.model_validate(decision_payload(thread))
        )
    with TestClient(create_app(repository)):
        detail = claims.detail("CLM-1001")
        assert detail["run_active"] is False
        assert detail["status"] == ("awaiting_sign_off" if committed else "waiting")
        assert bool(detail["decision"]) == committed


def test_failure_preserves_payment(repository):
    claims = Claims(repository)

    def runner(claim_id, thread_id):
        decision = DecisionInput.model_validate(
            decision_payload(thread_id, payout="420")
        )
        claims.decide(claim_id, decision)
        claims.pay(claim_id, decision.payout)
        raise RuntimeError("After payment")

    thread = claims.start("CLM-1001")
    worker.execute(claims, "CLM-1001", thread, runner)
    assert claims.detail("CLM-1001")["payment"]["amount"] == "420.00"
    assert claims.detail("CLM-1001")["status"] == "decided"


def test_default_runner(monkeypatch):
    monkeypatch.setattr(worker, "run", lambda c, t: f"{c}/{t}")
    assert worker.default_runner("claim", "thread") == "claim/thread"
