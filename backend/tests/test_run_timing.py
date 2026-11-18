"""Dispatch rows use their own wall interval and preserve closed outcomes."""

import pytest
from sqlalchemy import select, update

from backend import main, runs
from backend.database import engine, run_claims, work_slices
from backend.tests.claim_requests import decide, fail, pay, sign_off
from backend.tests.claim_requests import start as start_claim
from backend.tests.run_requests import member, start

CLAIM_ID = "CLM-1001"
BEGIN = "2026-11-10T10:00:00+00:00"
END = "2026-11-10T10:00:42.500000+00:00"


@pytest.fixture
def clock(monkeypatch):
    def set_time(value):
        monkeypatch.setattr(runs, "now", lambda: value)
        monkeypatch.setattr(main, "now", lambda: value)

    set_time("2026-11-10T09:00:00+00:00")
    return set_time


def work(client, run_id, slice_id="first", seconds=7):
    response = client.put(
        f"/api/claims/{CLAIM_ID}/work-slices/{slice_id}",
        json={"run_id": run_id, "seconds": seconds},
    )
    assert response.status_code == 200


def read_member(client, run_id):
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    return response.json()["members"][0]


def make_legacy(run_id):
    with engine().begin() as db:
        key = run_claims.c.run_id == run_id
        saved = db.execute(select(run_claims.c.result).where(key)).scalar_one()
        legacy = {
            k: saved[k]
            for k in ("claim_id", "claim_status", "decision", "payout", "reason")
        }
        db.execute(update(run_claims).where(key).values(result=legacy))


@pytest.mark.parametrize("decision", ["approve", "decline", "refer"])
def test_completed_duration_uses_actual_start_and_completion(client, clock, decision):
    run_id = start(client).json()["run_id"]
    member(client, run_id, CLAIM_ID)
    unstarted = read_member(client, run_id)
    assert unstarted["customer_name"] == "Maya Patel"
    assert unstarted["started_at"] is None
    assert unstarted["finished_at"] is None
    assert unstarted["elapsed_seconds"] is None
    clock(BEGIN)
    start_claim(client, CLAIM_ID)
    work(client, run_id)
    clock(END)
    decide(client, CLAIM_ID, decision, 420 if decision == "approve" else 0)
    if decision == "approve":
        assert read_member(client, run_id)["finished_at"] is None
        pay(client, CLAIM_ID, 420)
    completed = read_member(client, run_id)
    assert completed["started_at"] == BEGIN
    assert completed["finished_at"] == END
    assert completed["elapsed_seconds"] == 42.5
    assert completed["active_seconds"] == 7
    closed = client.post(f"/api/runs/{run_id}/finish").json()
    assert closed["members"][0] == completed


def test_retries_and_later_dispatch_have_separate_starts_and_measurements(
    client, clock
):
    first = start(client).json()["run_id"]
    member(client, first, CLAIM_ID)
    clock(BEGIN)
    start_claim(client, CLAIM_ID)
    work(client, first)
    fail(client, CLAIM_ID)
    clock("2026-11-10T10:00:10+00:00")
    start_claim(client, CLAIM_ID)
    work(client, first, "retry", seconds=3)
    member(client, first, CLAIM_ID)
    retried = read_member(client, first)
    assert retried["started_at"] == BEGIN
    assert retried["active_seconds"] == 10
    frozen = client.post(f"/api/runs/{first}/finish").json()
    clock("2026-11-10T11:00:00+00:00")
    second = start(client).json()["run_id"]
    member(client, second, CLAIM_ID)
    work(client, second, "resumed", seconds=2)
    clock("2026-11-10T11:00:12+00:00")
    decide(client, CLAIM_ID, "refer", 0)
    resumed = read_member(client, second)
    assert resumed["started_at"] == "2026-11-10T11:00:00+00:00"
    assert resumed["elapsed_seconds"] == 12
    assert resumed["active_seconds"] == 2
    assert client.get(f"/api/runs/{first}").json() == frozen


@pytest.mark.parametrize("payout", [420, 1350])
def test_unpaid_approvals_remain_unfinished_and_frozen(client, clock, payout):
    run_id = start(client).json()["run_id"]
    member(client, run_id, CLAIM_ID)
    clock(BEGIN)
    start_claim(client, CLAIM_ID)
    work(client, run_id)
    decide(client, CLAIM_ID, "approve", payout)
    member(client, run_id, CLAIM_ID, "finished")
    clock(END)
    frozen = client.post(f"/api/runs/{run_id}/finish").json()
    row = frozen["members"][0]
    assert row["started_at"] == BEGIN
    assert row["finished_at"] is row["elapsed_seconds"] is None
    clock("2026-11-10T11:00:00+00:00")
    if payout > 1000:
        sign_off(client, CLAIM_ID, "HND-01")
    else:
        pay(client, CLAIM_ID, payout)
    assert client.get(f"/api/runs/{run_id}").json() == frozen


@pytest.mark.parametrize(
    "state", ["selected", "skipped", "in_progress", "left_for_handler"]
)
def test_unfinished_members_do_not_invent_completion(client, clock, state):
    run_id = start(client).json()["run_id"]
    member(client, run_id, CLAIM_ID)
    clock(BEGIN)
    if state == "skipped":
        member(client, run_id, CLAIM_ID, "skipped")
    elif state != "selected":
        start_claim(client, CLAIM_ID)
        work(client, run_id)
        if state == "left_for_handler":
            fail(client, CLAIM_ID)
            start_claim(client, CLAIM_ID)
            fail(client, CLAIM_ID)
    row = read_member(client, run_id)
    assert row["started_at"] == (
        BEGIN if state in ("in_progress", "left_for_handler") else None
    )
    assert row["finished_at"] is row["elapsed_seconds"] is None
    stopped = client.post("/api/runs/stop")
    assert stopped.status_code == 200
    assert read_member(client, run_id) == row


@pytest.mark.parametrize("measured", [False, True])
@pytest.mark.parametrize("decision", ["approve", "decline"])
@pytest.mark.parametrize(
    "completion", [END, "2026-11-10T08:00:00+00:00", "2026-11-10T11:00:00+00:00"]
)
def test_legacy_snapshots_use_bulk_identity_and_only_in_run_timing(
    client, clock, measured, completion, decision
):
    run_id = start(client).json()["run_id"]
    member(client, run_id, CLAIM_ID)
    clock(BEGIN)
    start_claim(client, CLAIM_ID)
    if measured:
        work(client, run_id)
    clock(completion)
    decide(client, CLAIM_ID, decision, 420 if decision == "approve" else 0)
    if decision == "approve":
        pay(client, CLAIM_ID, 420)
    clock("2026-11-10T10:01:00+00:00")
    client.post(f"/api/runs/{run_id}/finish")
    make_legacy(run_id)
    row = read_member(client, run_id)
    assert row["customer_name"] == "Maya Patel"
    assert row["started_at"] == (BEGIN if measured else None)
    assert row["finished_at"] == (END if completion == END else None)
    assert row["elapsed_seconds"] == (42.5 if measured and completion == END else None)


def test_legacy_held_outcome_survives_later_payment(client, clock):
    run_id = start(client).json()["run_id"]
    member(client, run_id, CLAIM_ID)
    clock(BEGIN)
    start_claim(client, CLAIM_ID)
    work(client, run_id)
    decide(client, CLAIM_ID, "approve", 1350)
    clock(END)
    client.post(f"/api/runs/{run_id}/finish")
    make_legacy(run_id)
    before = read_member(client, run_id)
    sign_off(client, CLAIM_ID, "HND-01")
    assert read_member(client, run_id) == before
    assert before["claim_status"] == "waiting_for_sign_off"
    assert before["elapsed_seconds"] is None


@pytest.mark.parametrize(
    "invalid_start", ["2026-11-10T08:00:00+00:00", "2026-11-10T11:00:00+00:00"]
)
def test_work_outside_run_is_not_a_start(client, clock, invalid_start):
    run_id = start(client).json()["run_id"]
    member(client, run_id, CLAIM_ID)
    clock(BEGIN)
    start_claim(client, CLAIM_ID)
    work(client, run_id)
    with engine().begin() as db:
        db.execute(update(work_slices).values(started_at=invalid_start))
    assert read_member(client, run_id)["started_at"] is None


def test_real_endpoint_timestamps_retain_subsecond_completion(client):
    run_id = start(client).json()["run_id"]
    member(client, run_id, CLAIM_ID)
    start_claim(client, CLAIM_ID)
    work(client, run_id)
    decide(client, CLAIM_ID, "approve", 420)
    pay(client, CLAIM_ID, 420)
    row = read_member(client, run_id)
    assert row["finished_at"] is not None
    assert row["elapsed_seconds"] is not None
    assert row["elapsed_seconds"] >= 0
    assert "." in row["finished_at"]
