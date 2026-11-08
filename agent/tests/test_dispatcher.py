"""Dispatcher concurrency, retry and claim recovery against the real backend."""

from threading import Barrier, Lock

import pytest
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from agent import api, dispatcher, harness
from agent.tests.queue_setup import leave_waiting
from agent.tests.stubs import call, graph_on, record, stub
from backend.database import engine, run_claims, work_slices
from backend.tests.run_requests import configuration


@pytest.mark.parametrize("n", [1, 3, 25])
def test_concurrency_is_bounded_and_every_claim_is_selected_once(monkeypatch, n):
    barrier, lock = Barrier(n), Lock()
    active = peak = 0
    worked = []

    def work(claim_id, run_id):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            worked.append(claim_id)
        if len(worked) <= n:
            barrier.wait(timeout=10)
        with lock:
            active -= 1
        return "Done"

    monkeypatch.setattr(harness, "execute_claim", work)
    run = api.start_run(configuration(concurrency=n))
    result = dispatcher.dispatch(run["run_id"], n)
    assert peak == n
    assert len(set(worked)) == len(worked) == len(result["results"]) == 25
    expected = sorted(
        api.list_claims(), key=lambda c: (c["submitted_on"], c["claim_id"])
    )
    assert [m["claim_id"] for m in api.get_run(run["run_id"])["members"]] == [
        c["claim_id"] for c in expected
    ]


@pytest.mark.parametrize("error_type", [ModelRateLimitError, ModelTimeoutError])
def test_transient_retry_resumes_same_attempt_and_thread(
    monkeypatch, error_type, traces
):
    leave_waiting("CLM-1001")
    saver = InMemorySaver()
    models = iter(
        [stub(call("get_claim"), error_type("retry")), stub(record("approve", 420))]
    )
    monkeypatch.setattr(
        harness, "execution_graph", lambda: graph_on(next(models), saver)
    )
    run = api.start_run(configuration())
    dispatcher.dispatch(run["run_id"], 1)
    claim = api.get_claim("CLM-1001")
    assert (claim["status"], claim["attempts"], claim["payment"]["amount"]) == (
        "decided",
        1,
        420,
    )
    assert len(traces) == 2
    assert traces[0]["langfuse_session_id"] == traces[1]["langfuse_session_id"]
    assert all(t["langfuse_tags"] == [run["run_id"]] for t in traces)
    assert len(api.get_run(run["run_id"])["members"]) == 1


def test_exhaustion_keeps_progress_and_other_claims_finish(monkeypatch):
    leave_waiting("CLM-1001", "CLM-1002")
    saver = InMemorySaver()
    models = iter(
        [
            stub(*(ModelTimeoutError("timeout") for _ in range(3))),
            stub(record("refer", 0)),
        ]
    )
    # The failing graph reuses the same scripted model on its three resumptions.
    failing = graph_on(next(models), saver)
    graphs = iter([failing, failing, failing, graph_on(next(models), saver)])
    monkeypatch.setattr(harness, "execution_graph", lambda: next(graphs))
    run_id = api.start_run(configuration())["run_id"]
    dispatcher.dispatch(run_id, 1)
    assert api.get_claim("CLM-1001")["status"] == "in_progress"
    assert api.get_claim("CLM-1001")["attempts"] == 1
    assert api.get_claim("CLM-1002")["status"] == "decided"
    assert "timeout" in api.get_run(run_id)["members"][0]["error"]


@pytest.mark.parametrize("succeed", [True, False])
def test_ordinary_failure_retries_fresh_once(monkeypatch, succeed):
    leave_waiting("CLM-1001")
    replies = [
        RuntimeError("first"),
        record("refer", 0) if succeed else RuntimeError("second"),
    ]
    graph = graph_on(stub(*replies))
    monkeypatch.setattr(harness, "execution_graph", lambda: graph)
    run_id = api.start_run(configuration())["run_id"]
    dispatcher.dispatch(run_id, 1)
    claim = api.get_claim("CLM-1001")
    assert claim["attempts"] == 2
    assert claim["status"] == ("decided" if succeed else "left_for_handler")
    with engine().connect() as db:
        assert len(db.execute(select(run_claims)).all()) == 1


def test_empty_and_reserved_claims_are_skipped(monkeypatch):
    leave_waiting("CLM-1001")
    assert harness.reserve_claim("CLM-1001")
    try:
        run_id = api.start_run(configuration())["run_id"]
        assert dispatcher.dispatch(run_id, 25)["results"] == []
        assert harness.execute_claim("CLM-1001", run_id).startswith("Skipped:")
    finally:
        harness.release_claim("CLM-1001")
    finished = api.finish_run(run_id)
    assert finished["summary"] == "Worked 0 claims: 0 approved, 0 declined, 0 referred."


def test_graphs_have_distinct_savers_on_one_pool(monkeypatch):
    pool = object()
    monkeypatch.setattr(harness.checkpoints, "pool", pool)
    monkeypatch.setattr(harness, "PostgresSaver", lambda p: {"pool": p})
    monkeypatch.setattr(harness, "model", lambda: "stub")
    monkeypatch.setattr(harness, "claim_graph", lambda llm, saver: saver)
    first, second = harness.execution_graph(), harness.execution_graph()
    assert first is not second
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first.get("pool") is second.get("pool") is pool


def test_worker_rechecks_status_and_releases_on_failure(monkeypatch):
    graph = graph_on(stub(record("refer", 0)))
    monkeypatch.setattr(harness, "execution_graph", lambda: graph)
    harness.work_claim("CLM-1001", graph)
    assert harness.execute_claim("CLM-1001", "unused") == "No eligible work"
    assert not harness.is_reserved("CLM-1001")

    def fail():
        raise RuntimeError("pool unavailable")

    monkeypatch.setattr(harness, "execution_graph", fail)
    assert "left for a handler" in harness.execute_claim("CLM-1002", "unused")
    assert not harness.is_reserved("CLM-1002")


def test_get_state_failure_is_settled_and_retried_fresh(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(harness, "monotonic", lambda: elapsed[0])
    api.start_claim("CLM-1001", "broken-thread")
    broken = graph_on(stub())

    def fail_state(*args):
        elapsed[0] += 7
        raise RuntimeError("checkpoint read failed")

    monkeypatch.setattr(broken, "get_state", fail_state)
    graphs = iter([broken, graph_on(stub(record("refer", 0)))])
    monkeypatch.setattr(harness, "execution_graph", lambda: next(graphs))
    run_id = api.start_run(configuration())["run_id"]
    api.record_run_claim(run_id, "CLM-1001", "selected", None)
    assert "refer" in harness.execute_claim("CLM-1001", run_id)
    claim = api.get_claim("CLM-1001")
    assert claim["attempts"] == 2
    assert claim["thread_id"] != "broken-thread"
    with engine().connect() as db:
        slices = list(
            db.execute(
                select(work_slices.c.seconds).where(work_slices.c.run_id == run_id)
            ).scalars()
        )
    assert slices == [7, 0]
