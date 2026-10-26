import pytest
from fastapi.testclient import TestClient

from backend import worker
from backend.claims import Claims
from backend.main import create_app
from backend.models import DecisionInput
from backend.tests.conftest import decide_fixture, decision_payload


def test_start_run_and_completion(repository):
    claims = Claims(repository)

    def runner(claim_id, thread_id):
        assert claims.events(claim_id)[0]["status"] == "started"
        assert claims.detail(claim_id)["run_active"]
        decide_fixture(
            claims,
            claim_id,
            DecisionInput.model_validate(decision_payload(thread_id, payout="420")),
        )
        return "Complete"

    queue = worker.QueueWorker(claims, runner, lambda: True, lambda: None)
    detail = queue.step({"CLM-1001"})
    assert detail is not None
    assert detail["status"] == "decided"
    assert detail["run_active"] is False
    assert queue.step({"CLM-1001"}) is None


def test_failed_run_retry_and_redacted_error(repository):
    def failing(_claim_id, _thread_id):
        raise RuntimeError("secret provider details")

    claims = Claims(repository)
    queue = worker.QueueWorker(claims, failing, lambda: True, lambda: None)
    first = queue.step({"CLM-1001"})
    assert first is not None
    assert first["status"] == "waiting"
    assert first["decision"] is None
    events = claims.events("CLM-1001")
    assert events[-1]["status"] == "failed"
    assert "secret" not in str(events)
    assert queue.step({"CLM-1001"}) is None
    retry = worker.QueueWorker(claims, failing, lambda: True, lambda: None)
    second = retry.step({"CLM-1001"})
    assert second is not None
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
        decide_fixture(
            claims, "CLM-1001", DecisionInput.model_validate(decision_payload(thread))
        )
    with TestClient(create_app(repository, auto_run=False)):
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
        decide_fixture(claims, claim_id, decision)
        claims.pay(claim_id, decision.payout)
        raise RuntimeError("After payment")

    thread = claims.start("CLM-1001")
    worker.execute(claims, "CLM-1001", thread, runner)
    assert claims.detail("CLM-1001")["payment"]["amount"] == "420.00"
    assert claims.detail("CLM-1001")["status"] == "decided"


def test_default_runner(monkeypatch):
    monkeypatch.setattr(worker, "run", lambda c, t: f"{c}/{t}")
    assert worker.default_runner("claim", "thread") == "claim/thread"
