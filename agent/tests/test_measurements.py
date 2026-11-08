"""Usage is provider-reported and active time belongs to its current execution."""

import itertools
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from agent import api, harness
from agent.tests.queue_setup import leave_waiting
from agent.tests.stubs import call, graph_on, record, stub
from backend.database import engine, model_calls, work_slices


def rows(table):
    with engine().connect() as db:
        return [dict(row) for row in db.execute(select(table)).mappings()]


def test_provider_measurements_are_exact_and_idempotent(client):
    api.start_claim("CLM-1001", "thread")
    usage = {
        "reply_id": "provider-1",
        "run_id": None,
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
        "cost": 0.0000137,
    }
    api.record_model_call("CLM-1001", usage)
    api.record_model_call("CLM-1001", usage)
    api.record_model_call("CLM-1001", usage | {"reply_id": "provider-2"})
    saved = rows(model_calls)
    assert len(saved) == 2
    assert [row["cost"] for row in saved] == [0.0000137, 0.0000137]
    assert saved[0]["total_tokens"] == 10
    assert (
        client.post("/api/claims/CLM-1002/model-calls", json=usage).status_code == 409
    )
    assert client.post("/api/claims/missing/model-calls", json=usage).status_code == 404
    assert (
        client.post(
            "/api/claims/CLM-1001/model-calls", json=usage | {"run_id": "missing"}
        ).status_code
        == 404
    )


def test_model_node_records_usage_and_missing_metadata():
    reply = record("refer", 0)
    reply.id = "provider-123"
    reply.usage_metadata = {"input_tokens": 11, "output_tokens": 2, "total_tokens": 13}
    reply.response_metadata = {"token_usage": {"cost": 0.000041}}
    harness.work_claim("CLM-1001", graph_on(stub(reply)))
    saved = rows(model_calls)[0]
    assert (saved["reply_id"], saved["cost"], saved["input_tokens"]) == (
        "provider-123",
        0.000041,
        11,
    )
    harness.work_claim("CLM-1002", graph_on(stub(AIMessage(content="No decision"))))
    assert rows(model_calls)[1]["cost"] is None


def test_active_slices_exclude_wait_and_split_run_attribution(client, monkeypatch):
    clock = itertools.count()
    monkeypatch.setattr(harness, "monotonic", lambda: next(clock))
    first = api.start_run(1)["run_id"]
    second = api.start_run(1)["run_id"]
    saver = InMemorySaver()
    harness.work_claim(
        "CLM-1001", graph_on(stub(record("approve", 1350)), saver), first
    )
    before = rows(work_slices)[0]["seconds"]
    for _ in range(100):
        next(clock)
    client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"})
    harness.carry_on("CLM-1001", graph_on(stub(), saver), second)
    harness.work_claim("CLM-1002", graph_on(stub(record("refer", 0))))
    slices = rows(work_slices)
    assert [row["run_id"] for row in slices] == [first, second, None]
    assert slices[0]["seconds"] == before
    assert all(row["seconds"] < 20 for row in slices)
    assert [row["run_id"] for row in rows(model_calls)] == [first, None]


def test_failure_and_restart_keep_separate_active_slices(monkeypatch):
    clock = itertools.count()
    monkeypatch.setattr(harness, "monotonic", lambda: next(clock))
    saver = InMemorySaver()
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim("CLM-1001", graph_on(stub(KeyboardInterrupt()), saver))
    for _ in range(1000):
        next(clock)
    harness.carry_on("CLM-1001", graph_on(stub(RuntimeError("failed")), saver))
    harness.work_claim("CLM-1001", graph_on(stub(record("refer", 0)), saver))
    assert len(rows(work_slices)) == 3
    assert all(row["seconds"] < 20 for row in rows(work_slices))
    assert len({row["thread_id"] for row in rows(work_slices)}) == 2


def test_slice_cannot_change_owner_or_shrink(client):
    api.start_claim("CLM-1001", "t1")
    api.start_claim("CLM-1002", "t2")
    api.record_work_slice("CLM-1001", "slice", None, 3)
    api.record_work_slice("CLM-1001", "slice", None, 2)
    assert rows(work_slices)[0]["seconds"] == 3
    response = client.put(
        "/api/claims/CLM-1002/work-slices/slice", json={"run_id": None, "seconds": 4}
    )
    assert response.status_code == 409


def test_current_queue_attributes_its_measurements():
    leave_waiting("CLM-1001")
    harness.run_queue(
        1,
        stub(call("work_claim", claim_id="CLM-1001"), "Done"),
        graph_on(stub(record("refer", 0))),
    )
    run = api.get_latest_run()
    assert run is not None
    run_id = run["run_id"]
    assert {row["run_id"] for row in rows(model_calls)} == {run_id}
    assert {row["run_id"] for row in rows(work_slices)} == {run_id}


def test_measurement_failure_settles_and_does_not_mask_interrupt(monkeypatch):
    def unavailable(*args):
        raise RuntimeError("measurement unavailable")

    monkeypatch.setattr(api, "record_work_slice", unavailable)
    assert "back in the queue" in harness.work_claim("CLM-1001", graph_on(stub()))
    writes = itertools.count()

    def fails_at_end(*args):
        if next(writes):
            raise RuntimeError("measurement unavailable")

    monkeypatch.setattr(api, "record_work_slice", fails_at_end)
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim("CLM-1002", graph_on(stub(KeyboardInterrupt())))


def test_concurrent_measurement_replay_is_atomic():
    api.start_claim("CLM-1001", "thread")
    usage = {
        "reply_id": "one",
        "run_id": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cost": 0.0001,
    }

    def write(seconds):
        api.record_model_call("CLM-1001", usage)
        api.record_work_slice("CLM-1001", "same-slice", None, seconds)

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(write, [9, 3, 7, 2, 10, 1]))
    assert len(rows(model_calls)) == 1
    assert rows(work_slices)[0]["seconds"] == 10
