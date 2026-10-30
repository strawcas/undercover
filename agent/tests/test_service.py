from threading import Event
from time import monotonic, sleep
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent import main, queue, service, service_worker
from agent.graph import ClaimWorkflow
from agent.tests.queue_stubs import ScriptedQueueModel
from agent.tests.stubs import api_client, scripted_model
from backend import routes
from backend.claims import Claims
from backend.config import database_identity
from backend.executions import ExecutionInput, Executions
from backend.harness_models import LimitedScope, RunInput
from backend.runs import Runs
from backend.tests.conftest import begin_claim


def until(predicate):
    deadline = monotonic() + 8
    while not predicate():
        if monotonic() > deadline:
            raise AssertionError("The stub service did not finish its saved work")
        sleep(0.02)


@pytest.fixture
def connected(client, monkeypatch):
    for module in (main, queue, service, service_worker):
        monkeypatch.setattr(module, "backend", lambda: api_client(client))
    monkeypatch.setattr(main, "model", lambda: scripted_model(payout="1250.00"))
    return client


def test_service_stays_idle_and_exposes_required_operations(connected, repository):
    with TestClient(service.create_app(model_factory=ScriptedQueueModel)) as agent:
        health = agent.get("/health")
        assert health.json() == {"status": "ok"}
        assert health.headers["X-UnderCover-Database"] == database_identity(
            repository.path
        )
        assert health.headers["X-UnderCover-Service"] == "agent"
        assert connected.get("/api/agent-runs/current").json() == {"run": None}
        assert Claims(repository).events("CLM-1001") == []
        schema = agent.get("/openapi.json").json()
        operations = [op for path in schema["paths"].values() for op in path.values()]
        assert len({op["operationId"] for op in operations}) == 4
        assert all(len(op["description"].splitlines()) == 1 for op in operations)
        body = schema["components"]["schemas"]["RunInput"]
        assert set(body["required"]) == set(body["properties"])
        assert agent.post("/runs", json={"run_id": str(uuid4())}).status_code == 422
        assert agent.post("/claims/CLM-1001/resume").status_code == 409
        assert agent.post("/claims/missing/resume").status_code == 404
        assert agent.post("/runs/missing/stop").status_code == 404


def test_committed_sign_off_acknowledges_before_payment_and_duplicates_coalesce(
    connected, repository, monkeypatch
):
    release, paying = Event(), Event()
    original = ClaimWorkflow.pay

    def delayed_payment(workflow, state):
        paying.set()
        assert release.wait(5)
        return original(workflow, state)

    monkeypatch.setattr(ClaimWorkflow, "pay", delayed_payment)
    app = service.create_app(model_factory=ScriptedQueueModel)
    with TestClient(app) as agent:
        request = {
            "run_id": str(uuid4()),
            "scope": {"mode": "limited", "max_claims": 1},
        }
        result = agent.post("/runs", json=request)
        assert result.status_code == 202
        run_id = result.json()["run_id"]
        path = f"/api/agent-runs/{run_id}"
        until(lambda: connected.get(path).json()["status"] == "completed")
        parent = connected.get(path).json()
        assert parent["counts"]["awaiting_sign_off"] == 1
        notices = []

        def notify(claim_id):
            saved = Claims(repository).detail(claim_id)
            assert saved["sign_off"]["handler_id"] == "HND-01"
            assert saved["status"] == "in_progress"
            notices.append(agent.post(f"/claims/{claim_id}/resume").status_code)

        monkeypatch.setattr(routes, "notify_sign_off", notify)
        try:
            signed = connected.post(
                "/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"}
            )
            assert signed.status_code == 201
            assert notices == [202]
            assert paying.wait(2)
            assert Claims(repository).detail("CLM-1001")["payment"] is None
            for _ in range(3):
                assert agent.post("/claims/CLM-1001/resume").status_code == 202
            assert app.state.worker.jobs.qsize() == 0
            command = agent.post(
                "/runs",
                json={
                    "run_id": str(uuid4()),
                    "scope": {"mode": "limited", "max_claims": 1},
                },
            )
            assert command.status_code == 202
        finally:
            release.set()
        until(lambda: Claims(repository).detail("CLM-1001")["payment"] is not None)
        paid = Claims(repository).detail("CLM-1001")
        assert paid["payment"]["sign_off"] == signed.json()
        assert paid["thread_id"] == parent["attempts"][0]["thread_id"]
        assert connected.get(path).json() == parent
        until(lambda: app.state.worker.jobs.unfinished_tasks == 0)
        events = Claims(repository).events("CLM-1001")
        assert agent.post("/claims/CLM-1001/resume").status_code == 202
        assert Claims(repository).events("CLM-1001") == events


def test_stop_and_next_service_continue_original_scope(connected, repository):
    entered = Event()

    def interrupted(_claim, _thread, *, budget):
        entered.set()
        until(budget.cancelled.is_set)
        budget.check()
        return ""

    with TestClient(service.create_app(interrupted, ScriptedQueueModel)) as agent:
        request = {
            "run_id": str(uuid4()),
            "scope": {"mode": "limited", "max_claims": 1},
        }
        first = agent.post("/runs", json=request).json()
        assert entered.wait(3)
        assert (
            agent.post("/runs", json={**request, "run_id": str(uuid4())}).status_code
            == 409
        )
        assert agent.post(f"/runs/{first['run_id']}/stop").status_code == 202
        path = f"/api/agent-runs/{first['run_id']}"
        until(lambda: connected.get(path).json()["status"] == "interrupted")
    before = connected.get(path).json()
    thread = before["attempts"][0]["thread_id"]
    with TestClient(service.create_app(model_factory=ScriptedQueueModel)) as agent:
        continued = agent.post(
            "/runs", json={"run_id": str(uuid4()), "scope": {"mode": "all"}}
        )
        assert continued.status_code == 202
        assert continued.json()["run_id"] == first["run_id"]
        until(lambda: connected.get(path).json()["status"] == "completed")
    after = connected.get(path).json()
    assert after["attempts"][0]["thread_id"] == thread
    assert after["counts"]["assessment_attempts"] == 1
    assert after["counts"]["failed_assessments"] == 0
    assert after["claim_limit"] == 1


def test_service_rejects_mismatched_backend(connected, monkeypatch):
    monkeypatch.setattr(service, "database_identity", lambda _target: "wrong")
    with (
        pytest.raises(RuntimeError, match="another service or database"),
        TestClient(service.create_app()),
    ):
        pass


def test_only_one_service_owns_a_database(connected):
    with (
        TestClient(service.create_app()),
        pytest.raises(RuntimeError, match="already in use"),
        TestClient(service.create_app()),
    ):
        pass


def test_notification_ack_followed_by_service_failure_remains_recoverable(
    connected, repository, monkeypatch
):
    app = service.create_app(model_factory=ScriptedQueueModel)
    with TestClient(app) as agent:
        response = agent.post(
            "/runs",
            json={
                "run_id": str(uuid4()),
                "scope": {"mode": "claim", "claim_id": "CLM-1001"},
            },
        )
        run_id = response.json()["run_id"]
        until(
            lambda: (
                connected.get(f"/api/agent-runs/{run_id}").json()["status"]
                == "completed"
            )
        )
        connected.post(
            "/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"}
        ).raise_for_status()
        original = app.state.worker.continue_claim
        broken = Mock(side_effect=RuntimeError("PRIVATE-SERVICE-FAILURE"))
        monkeypatch.setattr(app.state.worker, "continue_claim", broken)
        assert agent.post("/claims/CLM-1001/resume").status_code == 202
        until(lambda: broken.called and app.state.worker.jobs.unfinished_tasks == 0)
        assert Claims(repository).detail("CLM-1001")["payment"] is None
        monkeypatch.setattr(app.state.worker, "continue_claim", original)
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE claims SET state='handler_review' WHERE id<>'CLM-1001'"
        )
    with TestClient(service.create_app(model_factory=ScriptedQueueModel)) as agent:
        response = agent.post(
            "/runs", json={"run_id": str(uuid4()), "scope": {"mode": "all"}}
        )
        assert response.status_code == 202
        until(lambda: Claims(repository).detail("CLM-1001")["payment"] is not None)
        assert Claims(repository).detail("CLM-1001")["failed_attempts"] == 0
        assert Claims(repository).detail("CLM-1001")["run_id"] == run_id


@pytest.mark.parametrize("reserved", [False, True])
def test_service_restart_recovers_registration_and_missing_first_checkpoint(
    connected, repository, reserved
):
    scope = LimitedScope(mode="limited", max_claims=1)
    thread = begin_claim(repository, scope=scope) if reserved else None
    if thread:
        parent = Runs(repository).current()
    else:
        parent, _ = Runs(repository).create(RunInput(run_id=uuid4(), scope=scope))
    assert parent is not None
    run_id = parent["run_id"]
    repository.add_claim(
        repository.record("claim", "CLM-1001") | {"claim_id": "TEST-LATER"}
    )
    with TestClient(service.create_app(model_factory=ScriptedQueueModel)) as agent:
        path = f"/api/agent-runs/{run_id}"
        paused = connected.get(path).json()
        assert paused["status"] == "interrupted"
        assert paused["counts"]["assessment_attempts"] == int(reserved)
        assert Claims(repository).detail("CLM-1001")["decision"] is None
        assert Claims(repository).detail("CLM-1001")["failed_attempts"] == 0
        result = agent.post(
            "/runs", json={"run_id": str(uuid4()), "scope": {"mode": "all"}}
        )
        assert result.status_code == 202
        assert result.json()["run_id"] == run_id
        until(lambda: connected.get(path).json()["status"] == "completed")
        after = connected.get(path).json()
        assert after["counts"]["assessment_attempts"] == 1
        assert after["counts"]["worked"] == 1
        assert after["counts"]["failed_assessments"] == 0
        if thread:
            assert after["attempts"][0]["thread_id"] == thread
        assert Claims(repository).detail("TEST-LATER")["status"] == "waiting"
        assert len(after["snapshot_claim_ids"]) == 25


def test_service_restart_releases_claim_execution_after_parent_completed(
    connected, repository
):
    parent = queue.run_queue({"mode": "claim", "claim_id": "CLM-1001"})
    claim = Claims(repository)
    claim.sign_off("CLM-1001", "HND-01")
    entry = parent.attempts[0]
    stale = Executions(repository).acquire(
        "CLM-1001",
        ExecutionInput.model_validate(
            {"thread_id": entry.thread_id, "owner_id": str(uuid4())}
        ),
    )
    with TestClient(service.create_app(model_factory=ScriptedQueueModel)) as agent:
        saved = connected.get("/api/claims/CLM-1001/execution").json()
        assert saved["status"] == "suspended"
        assert saved["owner_id"] is None
        assert saved["thread_id"] == stale.thread_id
        assert agent.post("/claims/CLM-1001/resume").status_code == 202
        until(lambda: claim.detail("CLM-1001")["payment"] is not None)
        assert claim.detail("CLM-1001")["failed_attempts"] == 0
        assert connected.get(
            f"/api/agent-runs/{parent.run_id}"
        ).json() == parent.model_dump(mode="json")


def test_failed_startup_reconciliation_releases_checkpoint_ownership(
    connected, monkeypatch
):
    original = service.request
    monkeypatch.setattr(
        service, "request", Mock(side_effect=RuntimeError("Backend unavailable"))
    )
    with (
        pytest.raises(RuntimeError, match="Backend unavailable"),
        TestClient(service.create_app()),
    ):
        pass
    monkeypatch.setattr(service, "request", original)
    with TestClient(service.create_app()) as agent:
        assert agent.get("/health").json() == {"status": "ok"}
