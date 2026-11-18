"""Run membership and summaries come from backend records."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, select, update

from backend import runs
from backend.database import claims, engine, run_claims
from backend.tests.run_requests import member, start


def test_run_lifecycle_and_frozen_results(client):
    assert client.get("/api/runs/latest").json() is None
    run = start(client).json()
    run_id = run["run_id"]
    assert run["members"] == []
    assert run["llm_cost"] == run["active_seconds"] == 0
    member(client, run_id, "CLM-1001")
    member(client, run_id, "CLM-1001")
    client.post("/api/claims/CLM-1001/start", json={"thread_id": "thread"})
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "approve",
            "payout": 1350,
            "section": "3",
            "reason": "Covered",
        },
    )
    member(client, run_id, "CLM-1001", "finished")
    finished = client.post(f"/api/runs/{run_id}/finish").json()
    assert len(finished["members"]) == 1
    assert "1 approved" in finished["summary"]
    assert "CLM-1001: $1,350.00 waiting for sign-off." in finished["summary"]
    client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"})
    assert client.get(f"/api/runs/{run_id}").json() == finished
    assert member(client, run_id, "CLM-1001").status_code == 409
    assert client.post(f"/api/runs/{run_id}/finish").status_code == 409


def test_completion_freezes_all_members_in_one_database_batch(client):
    run_id = start(client).json()["run_id"]
    ids = [row["claim_id"] for row in client.get("/api/claims").json()]
    client.put(f"/api/runs/{run_id}/selection", json={"claim_ids": ids})
    batches = []

    def capture(statement, parameters, executemany, **context):
        if statement.startswith("UPDATE run_claims"):
            batches.append((executemany, len(parameters)))

    database = engine()
    event.listen(database, "before_cursor_execute", capture, named=True)
    try:
        frozen = client.post(f"/api/runs/{run_id}/finish").json()
    finally:
        event.remove(database, "before_cursor_execute", capture)
    assert batches == [(True, 26)]
    assert len(frozen["members"]) == 26
    assert all(member["customer_name"] for member in frozen["members"])


def test_unknowns_and_membership_validation(client):
    assert client.get("/api/runs/unknown").status_code == 404
    assert member(client, "unknown", "CLM-1001").status_code == 404
    run_id = start(client).json()["run_id"]
    assert member(client, run_id, "unknown").status_code == 404
    assert member(client, run_id, "CLM-1001", "finished").status_code == 409
    for n in (0, 26):
        assert start(client, concurrency=n).status_code == 422
    assert start(client, kind="evaluation").status_code == 422
    assert start(client, set_name="evaluation").status_code == 422


def test_summary_and_startup_recovery(client):
    run_id = start(client).json()["run_id"]
    for claim_id in ("CLM-1001", "CLM-1002"):
        member(client, run_id, claim_id)
    client.post("/api/claims/CLM-1001/start", json={"thread_id": "keep"})
    assert client.post("/api/runs/stop").json() == []
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "stopped"
    assert "CLM-1001: in progress." in run["summary"]
    assert client.get("/api/claims/CLM-1001").json()["thread_id"] == "keep"
    second = start(client).json()
    assert client.get("/api/runs/latest").json()["run_id"] == second["run_id"]


def test_membership_is_atomic_across_concurrent_workers(client):
    run_id = start(client).json()["run_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: member(client, run_id, "CLM-1001"), range(4))
        )
    assert all(r.status_code == 200 for r in responses)
    assert len(client.get(f"/api/runs/{run_id}").json()["members"]) == 1


def test_running_members_are_live_and_closed_measurements_are_refused(client):
    run_id = start(client).json()["run_id"]
    member(client, run_id, "CLM-1001")
    client.post("/api/claims/CLM-1001/start", json={"thread_id": "thread"})
    member(client, run_id, "CLM-1001", "finished")
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "refer",
            "payout": 0,
            "section": "3",
            "reason": "Needs review",
        },
    )
    assert client.get(f"/api/runs/{run_id}").json()["members"][0]["decision"] == "refer"
    client.post(f"/api/runs/{run_id}/finish")
    body = {"run_id": run_id, "seconds": 99}
    assert (
        client.put("/api/claims/CLM-1001/work-slices/new", json=body).status_code == 409
    )


@pytest.mark.parametrize("lifecycle", ["running", "finished", "legacy"])
def test_run_read_query_count_does_not_grow_with_swarm_size(client, lifecycle):
    counts = []
    for size in (1, 25):
        run_id = start(client).json()["run_id"]
        for claim in client.get("/api/claims").json()[:size]:
            member(client, run_id, claim["claim_id"])
        if lifecycle != "running":
            client.post(f"/api/runs/{run_id}/finish")
        if lifecycle == "legacy":
            with engine().begin() as db:
                for saved in db.execute(
                    select(run_claims).where(run_claims.c.run_id == run_id)
                ).mappings():
                    old = {
                        key: value
                        for key, value in saved["result"].items()
                        if key
                        not in (
                            "customer_name",
                            "started_at",
                            "finished_at",
                            "elapsed_seconds",
                        )
                    }
                    db.execute(
                        update(run_claims)
                        .where(
                            run_claims.c.run_id == run_id,
                            run_claims.c.claim_id == saved["claim_id"],
                        )
                        .values(result=old)
                    )
        queries = []

        def record_query(*args, recorded=queries):
            recorded.append(args[2])

        db_engine = engine()
        event.listen(db_engine, "before_cursor_execute", record_query)
        try:
            assert client.get(f"/api/runs/{run_id}").status_code == 200
        finally:
            event.remove(db_engine, "before_cursor_execute", record_query)
        counts.append(len(queries))
    assert counts[0] == counts[1]
    assert counts[1] <= 6


def test_summary_and_members_use_the_same_frozen_outcomes(client, monkeypatch):
    run_id = start(client).json()["run_id"]
    member(client, run_id, "CLM-1001")
    client.post("/api/claims/CLM-1001/start", json={"thread_id": "thread"})
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "approve",
            "payout": 1350,
            "section": "3",
            "reason": "Covered",
        },
    )
    read = runs.get_run

    def sign_off_after_snapshot(db, selected_run):
        db.execute(
            update(claims)
            .where(claims.c.claim_id == "CLM-1001")
            .values(status="in_progress")
        )
        return read(db, selected_run)

    monkeypatch.setattr(runs, "get_run", sign_off_after_snapshot)
    result = client.post(f"/api/runs/{run_id}/finish").json()
    assert result["members"][0]["claim_status"] == "waiting_for_sign_off"
    assert "CLM-1001: $1,350.00 waiting for sign-off." in result["summary"]


def test_bulk_selection_is_ordered_idempotent_and_atomic(client):
    run_id = start(client).json()["run_id"]
    path = f"/api/runs/{run_id}/selection"
    body = {"claim_ids": ["CLM-1002", "CLM-1001", "CLM-1002"]}
    assert client.put(path, json=body).status_code == 200
    assert client.put(path, json=body).status_code == 200
    assert (
        client.put(path, json={"claim_ids": ["CLM-1003", "missing"]}).status_code == 404
    )
    run = client.get(f"/api/runs/{run_id}").json()
    assert [m["claim_id"] for m in run["members"]] == ["CLM-1002", "CLM-1001"]
    client.post(f"/api/runs/{run_id}/finish")
    assert client.put(path, json=body).status_code == 409
    assert client.put("/api/runs/missing/selection", json=body).status_code == 404


def test_bulk_selection_rejects_evaluation_copies_in_a_dispatch(client):
    evaluation = start(client, kind="evaluation", set_name="held_out", tiny=True).json()
    dispatch = start(client).json()
    copied_id = evaluation["members"][0]["claim_id"]
    response = client.put(
        f"/api/runs/{dispatch['run_id']}/selection",
        json={"claim_ids": ["CLM-1001", copied_id]},
    )
    assert response.status_code == 409
    assert client.get(f"/api/runs/{dispatch['run_id']}").json()["members"] == []


def test_bulk_selection_query_count_does_not_grow_with_claims(client):
    counts = []
    for size in (1, 25):
        run_id = start(client).json()["run_id"]
        ids = [c["claim_id"] for c in client.get("/api/claims").json()[:size]]
        queries = []

        def record_query(*args, recorded=queries):
            recorded.append(args[2])

        db_engine = engine()
        event.listen(db_engine, "before_cursor_execute", record_query)
        try:
            assert (
                client.put(
                    f"/api/runs/{run_id}/selection", json={"claim_ids": ids}
                ).status_code
                == 200
            )
        finally:
            event.remove(db_engine, "before_cursor_execute", record_query)
        counts.append(len(queries))
    assert counts[0] == counts[1]
    assert counts[1] <= 4
