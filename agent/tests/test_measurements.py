"""Usage is provider-reported and active time belongs to its current execution."""

import itertools
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import event, select

from agent import api, dispatcher, harness
from agent.tests.queue_setup import leave_waiting
from agent.tests.stubs import graph_on, record, stub
from backend.database import engine, model_calls, work_slices
from backend.tests.run_requests import configuration


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


def test_null_provider_usage_completes_with_unavailable_measurements():
    reply = record("refer", 0)
    reply.response_metadata = {"token_usage": None}
    harness.work_claim("CLM-1001", graph_on(stub(reply)))
    assert api.get_claim("CLM-1001")["status"] == "decided"
    saved = rows(model_calls)[0]
    assert saved["cost"] is saved["input_tokens"] is saved["total_tokens"] is None


def test_active_slices_exclude_wait_and_split_run_attribution(client, monkeypatch):
    clock = itertools.count()
    monkeypatch.setattr(harness, "monotonic", lambda: next(clock))
    first = api.start_run(configuration())["run_id"]
    second = api.start_run(configuration())["run_id"]
    saver = InMemorySaver()
    harness.work_claim(
        "CLM-1001", graph_on(stub(record("approve", 1350)), saver), first
    )
    before = rows(work_slices)[0]["seconds"]
    for _ in range(100):
        next(clock)
    client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"})
    harness.carry_on(api.get_claim("CLM-1001"), graph_on(stub(), saver), second)
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
    harness.carry_on(
        api.get_claim("CLM-1001"), graph_on(stub(RuntimeError("failed")), saver)
    )
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


def test_payment_resume_does_not_wait_for_instructions_or_telemetry(
    client, monkeypatch
):
    saver = InMemorySaver()
    harness.work_claim("CLM-1001", graph_on(stub(record("approve", 1350)), saver))
    client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"})
    elapsed = [0.0]
    monkeypatch.setattr(harness, "monotonic", lambda: elapsed[0])

    def prepare():
        elapsed[0] = 4.0
        graph = graph_on(stub(), saver)
        monkeypatch.setattr(graph, "get_state", unavailable)
        return graph

    def unavailable():
        raise AssertionError("Payment needs no instructions or duplicate state read")

    saved = []
    record_slice = api.record_work_slice

    def record_after_payment(*args):
        assert api.get_claim("CLM-1001")["payment"] is not None
        saved.append(args[-1])
        record_slice(*args)

    monkeypatch.setattr(harness, "execution_graph", prepare)
    monkeypatch.setattr(api, "get_instructions", unavailable)
    monkeypatch.setattr(api, "record_work_slice", record_after_payment)
    assert "approve" in harness.carry_on(api.get_claim("CLM-1001"), None)
    assert saved
    assert all(seconds == 4 for seconds in saved)


def test_work_slice_uses_one_owner_checked_monotonic_upsert(client):
    api.start_claim("CLM-1001", "thread")
    queries = []

    def record_query(*args):
        queries.append(args[2])

    db_engine = engine()
    event.listen(db_engine, "before_cursor_execute", record_query)
    try:
        counts = []
        for seconds in (3, 5, 2, 5):
            queries.clear()
            api.record_work_slice("CLM-1001", "slice", None, seconds)
            counts.append(len(queries))
        assert counts == [2, 2, 3, 3]
    finally:
        event.remove(db_engine, "before_cursor_execute", record_query)
    assert rows(work_slices)[0]["seconds"] == 5
    run = api.start_run(configuration())["run_id"]
    with pytest.raises(api.Refused) as refused:
        api.record_work_slice("CLM-1001", "slice", run, 6)
    assert refused.value.status_code == 409
    assert rows(work_slices)[0]["run_id"] is None


def test_dispatch_attributes_its_measurements(monkeypatch):
    leave_waiting("CLM-1001")
    monkeypatch.setattr(
        harness, "execution_graph", lambda: graph_on(stub(record("refer", 0)))
    )
    run_id = api.start_run(configuration())["run_id"]
    dispatcher.dispatch(run_id, 1)
    api.finish_run(run_id)
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
