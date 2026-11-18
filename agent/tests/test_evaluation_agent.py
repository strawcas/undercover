"""Evaluations use the normal claim agent and delete every copy checkpoint."""

import json
from contextlib import nullcontext

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from langchain_core.exceptions import ModelTimeoutError
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import update

from agent import api, checkpoints, dispatcher, harness, service
from agent.tests.records import latest
from agent.tests.stubs import call, graph_on, record, stub
from backend.database import engine, evaluation_labels


def test_service_works_copies_with_no_labels_and_clears_held_threads(
    client, monkeypatch
):
    before = api.list_claims()
    saver = InMemorySaver()
    models = []

    def build(version=None):
        llm = stub(
            call("get_claim"),
            call("get_policy", policy_id="POL-2001"),
            call("get_claim_history", customer_id="CUS-3001"),
            call("search_documents", query="delay"),
            call("read_section", document="policy_wording", number=1),
            call("calculate_payout", benefit="trip_delay", lines=[], paid_by_others=0),
            record("approve", 1350),
        )
        models.append(llm)
        return graph_on(llm, saver)

    monkeypatch.setattr(harness, "execution_graph", build)
    cleared = []

    def clear(threads):
        for thread in threads:
            saver.delete_thread(thread)
            cleared.append(thread)

    monkeypatch.setattr(checkpoints, "clear_threads", clear)
    request = api.backend.request
    paths = []

    def capture(method, path, **kwargs):
        paths.append(path)
        return request(method, path, **kwargs)

    monkeypatch.setattr(api.backend, "request", capture)
    with engine().begin() as db:
        db.execute(
            update(evaluation_labels).values(
                record={
                    "decision": "decline",
                    "payout": 0,
                    "reason": "SECRET_HANDLER_RATIONALE",
                    "reference": "SECRET_REFERENCE",
                }
            )
        )
    assert service.run_slot.acquire(blocking=False)
    service.work_run(
        {"kind": "evaluation", "set_name": "evaluation", "tiny": True, "concurrency": 1}
    )
    assert not service.run_slot.locked()
    result = client.get("/api/evaluations").json()[0]
    assert result["status"] == "finished"
    assert result["configuration_version"] == 1
    assert len(result["members"]) == len(cleared) == 5
    for member in result["members"]:
        copy = api.get_claim(member["claim_id"])
        assert copy["status"] == "waiting_for_sign_off"
        assert copy["payment"] is None
        assert saver.get_tuple(harness.thread_of(copy)) is None
    assert api.list_claims() == before
    assert not any(path.startswith("/evaluations") for path in paths)
    for model in models:
        prompts = json.dumps(
            [
                message.model_dump(mode="json")
                for prompt in model.prompts
                for message in prompt
            ]
        )
        assert "SECRET_HANDLER_RATIONALE" not in prompts
        assert "SECRET_REFERENCE" not in prompts
        assert '"handler_id"' not in prompts


def test_same_admission_slot_covers_both_kinds_before_background_creation(client):
    background = BackgroundTasks()
    service.start_evaluation(
        service.EvaluationRequest(set_name="held_out", tiny=True, concurrency=5),
        background,
    )
    try:
        with pytest.raises(HTTPException) as busy:
            service.start_dispatch(
                service.DispatchRequest(concurrency=25), BackgroundTasks()
            )
        assert busy.value.status_code == 409
        assert latest(client) is None
    finally:
        service.run_slot.release()


def test_failed_attempt_threads_are_all_returned_for_startup_cleanup(client):
    run = api.start_run(
        {
            "kind": "evaluation",
            "set_name": "held_out",
            "tiny": True,
            "concurrency": 5,
            "configuration_version": 1,
            "round_id": None,
        }
    )
    claim_id = run["members"][0]["claim_id"]
    api.start_claim(claim_id, "failed-attempt")
    api.fail_claim(claim_id, "Error before decision")
    api.start_claim(claim_id, "stopped-attempt")
    assert set(api.get_run_threads(run["run_id"])) == {
        "failed-attempt",
        "stopped-attempt",
    }
    assert set(api.stop_runs()) == {"failed-attempt", "stopped-attempt"}
    assert api.get_run(run["run_id"])["status"] == "stopped"


@pytest.mark.parametrize("transient", [False, True])
def test_exhausted_and_failed_copies_clear_all_attempts(client, monkeypatch, transient):
    before = api.list_claims()
    saver = InMemorySaver()
    if transient:
        failing = graph_on(
            stub(*(ModelTimeoutError("timeout") for _ in range(3))), saver
        )
        first = [failing] * 3
    else:
        first = [graph_on(stub(RuntimeError("failed")), saver) for _ in range(2)]
    graphs = iter(first + [graph_on(stub(record("refer", 0)), saver) for _ in range(4)])
    monkeypatch.setattr(harness, "execution_graph", lambda version=None: next(graphs))
    monkeypatch.setattr(checkpoints.state, "pool", object())
    monkeypatch.setattr(checkpoints, "PostgresSaver", lambda pool: saver)
    assert service.run_slot.acquire(blocking=False)
    service.work_run(
        {"kind": "evaluation", "set_name": "evaluation", "tiny": True, "concurrency": 1}
    )
    result = client.get("/api/evaluations").json()[0]
    first_copy = api.get_claim(result["members"][0]["claim_id"])
    assert first_copy["status"] == ("in_progress" if transient else "left_for_handler")
    assert first_copy["attempts"] == (1 if transient else 2)
    threads = api.get_run_threads(result["run_id"])
    assert len(threads) == (5 if transient else 6)
    assert all(
        saver.get_tuple({"configurable": {"thread_id": thread}}) is None
        for thread in threads
    )
    assert client.get(f"/api/claims/{first_copy['claim_id']}/steps").json()
    assert result["status"] == "finished"
    assert api.list_claims() == before


def test_startup_cleans_stopped_evaluation_threads_but_preserves_records(monkeypatch):
    run = api.start_run(
        {
            "kind": "evaluation",
            "set_name": "held_out",
            "tiny": True,
            "concurrency": 5,
            "configuration_version": 1,
            "round_id": None,
        }
    )
    claim_id = run["members"][0]["claim_id"]
    saver = InMemorySaver()
    harness.work_claim(
        claim_id, graph_on(stub(record("approve", 1350)), saver), run["run_id"]
    )
    before = api.get_claim(claim_id)
    assert saver.get_tuple(harness.thread_of(before)) is not None
    monkeypatch.setattr(service, "checkpointer", lambda: nullcontext(None))
    monkeypatch.setattr(checkpoints.state, "pool", object())
    monkeypatch.setattr(checkpoints, "PostgresSaver", lambda pool: saver)
    with TestClient(service.app) as agent:
        assert agent.get("/api/health").status_code == 200
    assert api.get_run(run["run_id"])["status"] == "stopped"
    assert api.get_claim(claim_id) == before
    assert saver.get_tuple(harness.thread_of(before)) is None
    later = api.start_run(
        {
            "kind": "dispatch",
            "set_name": None,
            "tiny": None,
            "concurrency": 25,
            "configuration_version": 1,
            "round_id": None,
        }
    )
    assert (
        claim_id
        not in dispatcher.list_work(
            {
                "run_id": later["run_id"],
                "claims": [],
                "results": [],
                "configuration_version": 1,
            }
        )["claims"]
    )
    assert api.get_run(run["run_id"])["status"] == "stopped"
