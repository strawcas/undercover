import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.claims import Claims
from backend.executions import (
    ExecutionInput,
    Executions,
    ReleaseInput,
    read_attempt,
    save_attempt,
)
from backend.models import DecisionInput
from backend.runs import Runs
from backend.tests.conftest import audit_decision, begin_claim, decision_payload


def test_paused_queue_cannot_admit_or_resume_undecided_work_without_command(
    client, repository
):
    thread = begin_claim(repository)
    run_id = Claims(repository).detail("CLM-1001")["run_id"]
    Runs(repository).recover()
    attempts = f"/api/agent-runs/{run_id}/attempts"
    for claim_id in ("CLM-1001", "CLM-1002"):
        response = client.post(
            attempts, json={"claim_id": claim_id, "thread_id": str(uuid4())}
        )
        assert response.status_code == 409
        assert "paused" in response.json()["detail"]
    owner = {"thread_id": thread, "owner_id": str(uuid4())}
    assert client.post("/api/claims/CLM-1001/execution", json=owner).status_code == 409
    assert Claims(repository).detail("CLM-1001")["failed_attempts"] == 0
    assert Claims(repository).detail("CLM-1002")["status"] == "waiting"
    resumed = client.post(
        "/api/agent-runs", json={"run_id": str(uuid4()), "scope": {"mode": "all"}}
    )
    assert resumed.json()["run_id"] == run_id
    client.headers["X-UnderCover-Run-Generation"] = str(
        resumed.json()["execution_generation"]
    )
    entry = client.post(
        attempts, json={"claim_id": "CLM-1001", "thread_id": str(uuid4())}
    )
    assert entry.status_code == 200
    assert entry.json()["thread_id"] == thread
    assert client.post("/api/claims/CLM-1001/execution", json=owner).status_code == 200


def test_legacy_decision_without_request_fields_stays_immutable(client, repository):
    thread = begin_claim(repository)
    legacy = decision_payload(thread)
    legacy.pop("manual_sections")
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE claims SET decision=? WHERE id='CLM-1001'", (json.dumps(legacy),)
        )
    result = client.post("/api/claims/CLM-1001/decision", json=decision_payload(thread))
    assert result.status_code == 409
    with repository.connect() as connection:
        assert (
            json.loads(
                connection.execute(
                    "SELECT decision FROM claims WHERE id='CLM-1001'"
                ).fetchone()[0]
            )
            == legacy
        )


def test_matching_financial_replays_preserve_records_and_events(client, repository):
    assert client.get("/api/claims/CLM-1001/execution").status_code == 404
    thread = begin_claim(repository)
    body = decision_payload(thread, payout="420.00")
    audit_decision(repository, "CLM-1001", DecisionInput.model_validate(body))
    first = client.post("/api/claims/CLM-1001/decision", json=body)
    assert first.status_code == 201
    decision = first.json()["decision"]
    for change in [
        {"calculation": "Different calculation"},
        {"payout": "421"},
        {"thread_id": str(uuid4())},
        {"policy_sections": ["3"]},
    ]:
        assert (
            client.post("/api/claims/CLM-1001/decision", json=body | change).status_code
            == 409
        )
    paid = client.post(
        "/api/claims/CLM-1001/agent-pay", json={"thread_id": thread, "amount": "420"}
    )
    assert paid.status_code == 201
    Claims(repository).finish("CLM-1001", thread)
    current = client.get("/api/claims/CLM-1001/execution")
    assert current.status_code == 200
    assert current.json()["thread_id"] == thread
    events = Claims(repository).events("CLM-1001")
    assert (
        client.post("/api/claims/CLM-1001/decision", json=body).json()["decision"]
        == decision
    )
    assert (
        client.post("/api/claims/CLM-1001/pay", json={"amount": "420.00"}).json()[
            "payment"
        ]
        == paid.json()["payment"]
    )
    assert (
        client.post("/api/claims/CLM-1001/pay", json={"amount": "421"}).status_code
        == 409
    )
    assert Claims(repository).events("CLM-1001") == events


def test_new_execution_fences_old_writes_and_finalization(client, repository):
    thread = begin_claim(repository)
    base = "/api/claims/CLM-1001/execution"
    owner = dict(thread_id=thread, owner_id=str(uuid4()))
    first = client.post(base, json=owner).json()
    first_id = first["execution_id"]
    assert client.post(base, json=owner).json()["execution_id"] == first_id
    assert client.post(base, json=owner | {"owner_id": str(uuid4())}).status_code == 409
    assert (
        client.post(
            f"{base}/{first_id}/release", json=owner | {"mode": "suspend"}
        ).status_code
        == 200
    )
    second_owner = owner | {"owner_id": str(uuid4())}
    second = client.post(base, json=second_owner).json()
    assert second["execution_id"] != first_id
    assert second["attempt_number"] == 1
    body = decision_payload(thread, payout="420")
    audit_decision(repository, "CLM-1001", DecisionInput.model_validate(body))
    old = {"X-UnderCover-Execution": first_id}
    current = {"X-UnderCover-Execution": second["execution_id"]}
    assert (
        client.post("/api/claims/CLM-1001/decision", json=body, headers=old).status_code
        == 409
    )
    assert (
        client.post(
            "/api/claims/CLM-1001/decision", json=body, headers=current
        ).status_code
        == 201
    )
    assert client.put(f"{base}/{first_id}", json=owner).status_code == 409
    finish = f"/api/agent-runs/{second['run_id']}/attempts/{thread}/finish"
    assert client.post(finish, json={"error": ""}, headers=old).status_code == 409
    assert (
        client.post(
            "/api/claims/CLM-1001/agent-pay",
            json={"thread_id": thread, "amount": "420"},
            headers=current,
        ).status_code
        == 201
    )
    assert client.post(finish, json={"error": ""}, headers=current).status_code == 200
    assert Claims(repository).detail("CLM-1001")["failed_attempts"] == 0


def test_suspended_time_does_not_use_remaining_active_budget(repository):
    thread = begin_claim(repository)
    executions = Executions(repository)
    owner = ExecutionInput(thread_id=thread, owner_id=uuid4())
    active = executions.acquire("CLM-1001", owner)
    assert active.execution_id is not None
    executions.release(
        "CLM-1001",
        active.execution_id,
        ReleaseInput(thread_id=thread, owner_id=owner.owner_id, mode="suspend"),
    )
    with repository.transaction() as connection:
        saved = read_attempt(connection, thread)
        saved.remaining_seconds = 123
        saved.deadline_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        save_attempt(connection, saved)
    resumed = executions.acquire(
        "CLM-1001", ExecutionInput(thread_id=thread, owner_id=uuid4())
    )
    assert resumed.remaining_seconds == 123
    assert resumed.deadline_at > datetime.now(UTC).isoformat()
    assert resumed.attempt_number == 1
