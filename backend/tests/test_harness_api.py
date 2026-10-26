from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.claims import Claims
from backend.harness_models import AttemptInput, RunFinishInput, RunInput, TodosInput
from backend.models import DecisionInput
from backend.reset_db import rebuild
from backend.runs import Runs
from backend.storage import ROOT
from backend.tests.conftest import audit_decision, decision_payload


def register(client, scope=None):
    payload = {"run_id": str(uuid4()), "scope": scope or {"mode": "all"}}
    response = client.post("/api/agent-runs", json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


def plan_claims(client, run_id, ids):
    body = {
        "items": [
            dict(id=f"claim:{key}", claim_id=key, text=f"Assess {key}", status="to_do")
            for key in ids
        ]
    }
    response = client.put(f"/api/agent-runs/{run_id}/todos", json=body)
    assert response.status_code == 200, response.text
    return body


def reserve(client, run_id, claim_id):
    payload = {"claim_id": claim_id, "thread_id": str(uuid4())}
    response = client.post(f"/api/agent-runs/{run_id}/attempts", json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


def finish_attempt(client, entry, error=""):
    path = f"/api/agent-runs/{entry['run_id']}/attempts/{entry['thread_id']}/finish"
    return client.post(path, json={"error": error})


def test_empty_current_registration_scope_and_replay(client):
    assert client.get("/api/agent-runs/current").json() == {"run": None}
    data, payload = register(client, {"mode": "limited", "max_claims": 5})
    assert data["claim_limit"] == data["capacity_remaining"] == 5
    assert len(data["snapshot_claim_ids"]) == 25
    assert data["counts"]["worked"] == 0
    assert client.get("/api/agent-runs/current").json()["run"] == data
    assert client.get(f"/api/agent-runs/{data['run_id']}").json() == data
    replay = client.post("/api/agent-runs", json=payload)
    assert replay.status_code == 200
    assert replay.json() == data
    assert (
        client.post(
            "/api/agent-runs", json=payload | {"scope": {"mode": "all"}}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/agent-runs", json=payload | {"run_id": str(uuid4())}
        ).status_code
        == 409
    )
    check = client.get(f"/api/agent-runs/{data['run_id']}/completion-check").json()
    assert not check["complete"]
    assert check["missing_claim_ids"] == data["snapshot_claim_ids"]
    assert (
        client.post(
            f"/api/agent-runs/{data['run_id']}/finish",
            json={"status": "completed", "detail": ""},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("cap", [0, -1, True, 1.5, "5"])
def test_strict_scope_caps(client, cap):
    response = client.post(
        "/api/agent-runs",
        json={
            "run_id": str(uuid4()),
            "scope": {"mode": "limited", "max_claims": cap},
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "scope",
    [
        {},
        {"mode": "unknown"},
        {"mode": "limited"},
        {"mode": "claim"},
        {"mode": "all", "max_claims": 5},
    ],
)
def test_scope_contracts_reject_missing_or_extra_inputs(client, scope):
    assert (
        client.post(
            "/api/agent-runs",
            json={
                "run_id": str(uuid4()),
                "scope": scope,
            },
        ).status_code
        == 422
    )


def test_atomic_admission_and_attempt_replays(client):
    run, _ = register(client, {"mode": "limited", "max_claims": 2})
    key = run["run_id"]
    plan_claims(client, key, ["CLM-1003", "CLM-1001"])
    entry, payload = reserve(client, key, "CLM-1003")
    assert entry["attempt_number"] == 1
    assert entry["deadline_at"] > entry["started_at"]
    assert client.get(f"/api/agent-runs/{key}").json()["admitted_claim_ids"] == [
        "CLM-1003"
    ]
    assert (
        client.post(f"/api/agent-runs/{key}/attempts", json=payload).status_code == 200
    )
    assert (
        client.post(
            f"/api/agent-runs/{key}/attempts",
            json={
                "claim_id": "CLM-1001",
                "thread_id": str(uuid4()),
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/agent-runs/{key}/attempts",
            json=payload
            | {
                "claim_id": "CLM-1001",
            },
        ).status_code
        == 409
    )
    result = finish_attempt(client, entry, "Synthetic timeout")
    assert result.status_code == 200
    assert result.json()["claim_status"] == "waiting"
    assert result.json()["failed_attempts"] == 1
    assert finish_attempt(client, entry, "Synthetic timeout").json() == result.json()
    assert finish_attempt(client, entry, "Changed error").status_code == 409
    retry, _ = reserve(client, key, "CLM-1003")
    result = finish_attempt(client, retry)
    assert result.json()["error"] == "No decision was recorded."
    assert result.json()["claim_status"] == "handler_review"
    assert result.json()["failed_attempts"] == 2
    check = client.get(f"/api/agent-runs/{key}/completion-check").json()
    assert check["capacity_remaining"] == 1
    assert "CLM-1003" not in check["retryable_claim_ids"]
    assert (
        client.get(f"/api/agent-runs/{key}").json()["todos"][0]["handoff_reason"]
        == "technical_failure"
    )


def test_five_distinct_claim_cap_and_two_failures(client):
    run, _ = register(client, {"mode": "limited", "max_claims": 5})
    key = run["run_id"]
    chosen = list(reversed(run["snapshot_claim_ids"][:5]))
    plan_claims(client, key, chosen)
    for claim_id in chosen:
        for _ in range(2):
            entry, _ = reserve(client, key, claim_id)
            assert finish_attempt(client, entry, "Synthetic failure").status_code == 200
    candidate = run["snapshot_claim_ids"][5]
    response = client.post(
        f"/api/agent-runs/{key}/attempts",
        json={
            "claim_id": candidate,
            "thread_id": str(uuid4()),
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Run claim limit reached."
    assert client.get(f"/api/claims/{candidate}").json()["status"] == "waiting"
    check = client.get(f"/api/agent-runs/{key}/completion-check").json()
    assert check["complete"]
    assert check["missing_claim_ids"] == check["retryable_claim_ids"] == []
    result = client.post(
        f"/api/agent-runs/{key}/finish", json={"status": "completed", "detail": ""}
    )
    assert result.status_code == 200
    assert result.json()["admitted_claim_ids"] == chosen
    assert result.json()["counts"] | {} == {
        "worked": 5,
        "assessment_attempts": 10,
        "failed_assessments": 10,
        "approved": 0,
        "declined": 0,
        "referred": 0,
        "simulated_payments": 0,
        "awaiting_sign_off": 0,
        "handler_review": 5,
        "unpaid_approvals": 0,
        "waiting_remaining": 20,
    }
    assert (
        client.post(
            f"/api/agent-runs/{key}/finish", json={"status": "completed", "detail": ""}
        ).json()
        == result.json()
    )
    assert (
        client.post(
            f"/api/agent-runs/{key}/finish",
            json={"status": "failed", "detail": "Changed"},
        ).status_code
        == 409
    )
    assert (
        client.put(f"/api/agent-runs/{key}/todos", json={"items": []}).status_code
        == 409
    )


def test_todo_validation_preserves_identity_and_truth(client):
    run, _ = register(client, {"mode": "claim", "claim_id": "CLM-1001"})
    key = run["run_id"]
    body = plan_claims(client, key, ["CLM-1001"])
    assert (
        client.put(
            f"/api/agent-runs/{key}/todos", json={"items": body["items"] * 2}
        ).status_code
        == 422
    )
    false_done = body["items"][0] | {"status": "done"}
    assert (
        client.put(
            f"/api/agent-runs/{key}/todos", json={"items": [false_done]}
        ).status_code
        == 409
    )
    entry, _ = reserve(client, key, "CLM-1001")
    assert (
        client.put(f"/api/agent-runs/{key}/todos", json={"items": []}).status_code
        == 409
    )
    changed = body["items"][0] | {"id": "new", "status": "in_progress"}
    assert (
        client.put(
            f"/api/agent-runs/{key}/todos", json={"items": [changed]}
        ).status_code
        == 409
    )
    assert finish_attempt(client, entry, "Synthetic failure").status_code == 200


@pytest.mark.parametrize(
    ("outcome", "payout", "expected"),
    [
        ("decline", "0", "done"),
        ("refer", "0", "left_for_handler"),
        ("approve", "1250", "left_for_handler"),
        ("approve", "420", "done"),
    ],
)
def test_saved_outcomes_and_frozen_handover(
    client, repository, outcome, payout, expected
):
    run, _ = register(client, {"mode": "claim", "claim_id": "CLM-1001"})
    key = run["run_id"]
    plan_claims(client, key, ["CLM-1001"])
    entry, _ = reserve(client, key, "CLM-1001")
    payload = decision_payload(entry["thread_id"], outcome, payout)
    audit_decision(repository, "CLM-1001", DecisionInput.model_validate(payload))
    assert client.post("/api/claims/CLM-1001/decision", json=payload).status_code == 201
    if payout == "420":
        Claims(repository).pay("CLM-1001", Decimal(payout))
    assert finish_attempt(client, entry).status_code == 200
    response = client.post(
        f"/api/agent-runs/{key}/finish", json={"status": "completed", "detail": ""}
    )
    assert response.status_code == 200
    assert response.json()["todos"][0]["status"] == expected
    if payout == "1250":
        Claims(repository).sign_off("CLM-1001", "HND-01")
        Claims(repository).pay("CLM-1001", Decimal(payout))
    assert client.get(f"/api/agent-runs/{key}").json() == response.json()
    assert finish_attempt(client, entry).status_code == 200
    assert client.get(f"/api/agent-runs/{key}").json() == response.json()


def test_recovery_is_idempotent_across_runs(client, repository):
    run, _ = register(client, {"mode": "claim", "claim_id": "CLM-1001"})
    key = run["run_id"]
    plan_claims(client, key, ["CLM-1001"])
    reserve(client, key, "CLM-1001")
    claims = Claims(repository)
    claims.recover_interrupted()
    frozen = client.get(f"/api/agent-runs/{key}").json()
    assert frozen["status"] == "interrupted"
    assert frozen["todos"][0]["status"] == "to_do"
    claims.recover_interrupted()
    assert client.get(f"/api/agent-runs/{key}").json() == frozen
    new, _ = register(client, {"mode": "claim", "claim_id": "CLM-1001"})
    plan_claims(client, new["run_id"], ["CLM-1001"])
    retry, _ = reserve(client, new["run_id"], "CLM-1001")
    assert retry["attempt_number"] == 2
    assert (
        finish_attempt(client, retry, "Second failure").json()["claim_status"]
        == "handler_review"
    )
    Runs(repository).finish(
        new["run_id"], RunFinishInput(status="completed", detail="")
    )
    assert (
        client.get("/api/agent-runs/current").json()["run"]["run_id"] == new["run_id"]
    )
    # An old replay must not make that run current or rewrite its snapshot.
    old = frozen["attempts"][0]
    assert (
        finish_attempt(client, old, "Backend restarted during this run.").status_code
        == 200
    )
    assert (
        client.get("/api/agent-runs/current").json()["run"]["run_id"] == new["run_id"]
    )
    assert (
        client.post(
            "/api/agent-runs",
            json={
                "run_id": str(uuid4()),
                "scope": {"mode": "claim", "claim_id": "CLM-1001"},
            },
        ).status_code
        == 409
    )


def test_empty_scope_unknown_resources_and_required_models(client, repository):
    with repository.transaction() as connection:
        connection.execute("UPDATE claims SET state='handler_review'")
    run, _ = register(client)
    assert run["claim_limit"] == 0
    assert (
        client.post(
            f"/api/agent-runs/{run['run_id']}/finish",
            json={"status": "completed", "detail": ""},
        ).status_code
        == 200
    )
    assert client.get(f"/api/agent-runs/{uuid4()}").status_code == 404
    assert client.get("/api/agent-runs/not-a-uuid").status_code == 422
    assert (
        client.post(
            "/api/agent-runs",
            json={
                "run_id": str(uuid4()),
                "scope": {"mode": "claim", "claim_id": "missing"},
            },
        ).status_code
        == 404
    )
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    for name in (
        "RunInput",
        "AllScope",
        "LimitedScope",
        "ClaimScope",
        "TodoInput",
        "TodosInput",
        "AttemptInput",
        "AttemptFinishInput",
        "RunFinishInput",
    ):
        assert set(schema[name]["properties"]) == set(schema[name]["required"])


def test_harness_migration_keeps_saved_pilot_work(repository):
    claims = Claims(repository)
    thread = claims.start("CLM-1001")
    claims.decide(
        "CLM-1001",
        DecisionInput.model_validate(decision_payload(thread)),
    )
    claims.finish("CLM-1001", thread)
    before = claims.detail("CLM-1001")
    activity = claims.events("CLM-1001")
    with repository.transaction() as connection:
        for statement in (
            "DROP TABLE agent_runs",
            "DROP TABLE agent_attempts",
            "DROP TABLE evidence",
            "DROP TABLE reference_sections",
            "ALTER TABLE claims DROP COLUMN run_id",
            "ALTER TABLE claims DROP COLUMN failed_attempts",
            "ALTER TABLE claims DROP COLUMN review_reason",
            "DELETE FROM records WHERE kind='settings' "
            "AND id IN ('manual','queue_instructions')",
        ):
            connection.execute(statement)
    repository.initialize()
    repository.initialize()
    assert claims.detail("CLM-1001") == before
    assert claims.events("CLM-1001") == activity
    assert claims.store.record("settings", "manual") == (
        ROOT / "data/claims_manual.md"
    ).read_text(encoding="utf-8")
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT text FROM reference_sections WHERE source='claims_manual' "
            "AND section_id='7.wild-animals'"
        ).fetchone()
        assert "Monkeys" in row["text"]


def test_concurrent_reservations_share_one_active_assessment(client):
    run, _ = register(client)
    run_id = run["run_id"]
    plan_claims(client, run_id, ["CLM-1001", "CLM-1002"])

    def attempt(claim_id):
        return client.post(
            f"/api/agent-runs/{run_id}/attempts",
            json={
                "claim_id": claim_id,
                "thread_id": str(uuid4()),
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(attempt, ["CLM-1001", "CLM-1002"]))
    assert sorted(statuses) == [201, 409]
    assert (
        client.get(f"/api/agent-runs/{run_id}").json()["counts"]["assessment_attempts"]
        == 1
    )


def test_rebuild_clears_harness_state(repository):
    runs = Runs(repository)
    data, _ = runs.create(
        RunInput.model_validate(
            {
                "run_id": str(uuid4()),
                "scope": {"mode": "claim", "claim_id": "CLM-1001"},
            }
        )
    )
    runs.todos(
        data["run_id"],
        TodosInput.model_validate(
            {
                "items": [
                    {
                        "id": "claim-1",
                        "claim_id": "CLM-1001",
                        "text": "Assess",
                        "status": "to_do",
                    }
                ]
            }
        ),
    )
    entry, _ = runs.start(
        data["run_id"], AttemptInput(claim_id="CLM-1001", thread_id=uuid4())
    )
    runs.finish_attempt(data["run_id"], entry["thread_id"], "Synthetic timeout")
    assert rebuild(repository.path) == 25
    assert Runs(repository).current() is None
    assert Claims(repository).detail("CLM-1001")["failed_attempts"] == 0
    with repository.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM agent_attempts").fetchone()[0] == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
