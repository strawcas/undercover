"""Durable four-tool improvement, approvals, nested runs and service admission."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event

import pytest
from fastapi import BackgroundTasks
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import (
    api,
    checkpoints,
    dispatcher,
    improver,
    improver_api,
    models,
    run_execution,
    service,
)
from agent.backend_client import Refused
from agent.harness import claim_config
from agent.tests.stubs import call, stub
from backend.tests.improvement_support import (
    baseline,
    bill_run,
    decide,
    freeze,
    new_round,
    proposal,
)
from backend.tests.run_requests import configuration


def paid(reply):
    """Supply the billing metadata returned by a provider without charging anything."""
    message = AIMessage(content=reply) if isinstance(reply, str) else reply
    message.response_metadata = {"token_usage": {"cost": 0.001}}
    return message


def graph_for(current, model, saver=None):
    report = improver_api.read_results(current["round_id"])
    return improver.build_graph(
        current, report["recipe"]["recipe"], saver or InMemorySaver(), model
    )


def test_invalid_change_returns_to_model_and_rejection_reaches_next_stretch(
    client, traces
):
    current = new_round(client)
    baseline(client, current)
    args = proposal(client, current)["arguments"]
    invalid = args | {"model": "not-allowed"}
    model = stub(
        paid(call("read_results")),
        paid(call("propose_change", **invalid)),
        paid(call("propose_change", **args)),
        paid(call("propose_change", **(args | {"reason": "Address handler feedback"}))),
        paid("No further change"),
    )
    saver = InMemorySaver()
    graph = graph_for(current, model, saver)
    improver.work(current["round_id"], graph=graph)
    pending = improver_api.get_round(current["round_id"])
    assert pending["status"] == "awaiting_configuration"
    assert len(pending["proposals"]) == 1
    assert len(model.prompts) == 3
    assert "allowed" in str(model.prompts[-1])
    first = pending["proposals"][0]
    decide(
        client, current, first, "reject", "Explain the general rule"
    ).raise_for_status()
    # A new graph/saver instance reads the same durable thread after a restart.
    restarted = graph_for(current, model, saver)
    improver.work(
        current["round_id"],
        {"type": "reject", "message": "Explain the general rule"},
        restarted,
    )
    pending = improver_api.get_round(current["round_id"])
    assert len(pending["proposals"]) == 2
    assert any(
        isinstance(message, ToolMessage)
        and "Explain the general rule" in message.content
        for message in model.prompts[-1]
    )
    replacement = pending["proposals"][-1]
    decide(client, current, replacement).raise_for_status()
    improver.work(
        current["round_id"], {"type": "approve"}, graph_for(current, model, saver)
    )
    finished = improver_api.get_round(current["round_id"])
    assert finished["status"] == "finished"
    assert finished["verdict"] == "rolled_back"
    assert api.get_current_configuration()["version"] == 1
    assert {trace["langfuse_session_id"] for trace in traces} == {current["round_id"]}
    assert finished["spending"]["improver"] == pytest.approx(0.005)
    assert len(model.prompts) == 5
    assert [tool.name for tool in improver.IMPROVER_TOOLS] == [
        "read_results",
        "propose_change",
        "run_evaluation",
        "propose_recipe",
    ]
    assert "never name an individual claim" in model.prompts[0][0].content


def test_baseline_runs_only_missing_sets_and_candidate_runs_three(client, monkeypatch):
    current = new_round(client)
    freeze(current["round_id"], set_name="evaluation")
    calls = []

    def execute(selection):
        calls.append(selection)
        identity = freeze(
            selection["round_id"],
            selection["configuration_version"],
            selection["set_name"],
        )
        bill_run(identity, 0.025)
        return {"status": "finished", "run_id": identity}

    monkeypatch.setattr(run_execution, "execute", execute)
    args = proposal(client, current)["arguments"]
    model = stub(
        paid(call("propose_change", **args)),
        paid(call("run_evaluation")),
        paid(
            call("propose_recipe", recipe="A better recipe", reason="Use the results")
        ),
        paid("Finished"),
    )
    graph = graph_for(current, model)
    improver.work(current["round_id"], graph=graph)
    assert [value["set_name"] for value in calls] == ["held_out", "held_out"]
    change = improver_api.get_round(current["round_id"])["proposals"][0]
    decide(client, current, change).raise_for_status()
    improver.work(current["round_id"], {"type": "approve"}, graph)
    assert [value["set_name"] for value in calls][2:] == [
        "evaluation",
        "held_out",
        "held_out",
    ]
    assert all(value["configuration_version"] == 2 for value in calls[2:])
    assert all(value["tiny"] is False and value["concurrency"] == 25 for value in calls)
    recipe = improver_api.get_round(current["round_id"])["proposals"][-1]
    assert recipe["kind"] == "recipe"
    decide(client, current, recipe).raise_for_status()
    improver.work(current["round_id"], {"type": "approve"}, graph)
    assert improver_api.get_round(current["round_id"])["status"] == "finished"


def test_call_cap_survives_repeated_approval_stretches(client, monkeypatch):
    current = new_round(client)
    baseline(client, current)
    args = proposal(client, current)["arguments"]
    monkeypatch.setattr(improver, "ROUND_MODEL_CALLS", 2)
    model = stub(
        paid(call("propose_change", **args)),
        paid(call("read_results")),
        paid("A third call must not happen"),
    )
    graph = graph_for(current, model)
    improver.work(current["round_id"], graph=graph)
    change = improver_api.get_round(current["round_id"])["proposals"][0]
    decide(client, current, change, "reject", "Try a broader rule").raise_for_status()
    improver.work(
        current["round_id"], {"type": "reject", "message": "Try a broader rule"}, graph
    )
    assert len(model.prompts) == 2
    assert improver_api.get_round(current["round_id"])["candidate_version"] is None


def test_busy_decision_writes_nothing_and_pause_frees_slot(client, monkeypatch):
    current = new_round(client)
    baseline(client, current)
    pending = proposal(client, current)
    improver_api.pending(current["round_id"], pending)
    background = BackgroundTasks()
    service.start_dispatch(service.DispatchRequest(concurrency=1), background)
    try:
        with pytest.raises(Refused, match="dispatch") as busy:
            service.decide_proposal(
                current["round_id"],
                pending["proposal_id"],
                service.HandlerDecision(
                    decision="approve",
                    reason=None,
                    handler=client.get("/api/me").json(),
                ),
                BackgroundTasks(),
            )
        assert busy.value.status_code == 409
        assert (
            improver_api.get_round(current["round_id"])["proposals"][0]["decision"]
            is None
        )
    finally:
        service.run_slot.release()
    monkeypatch.setattr(improver, "work", lambda *args: None)
    service.reserve("improvement round")
    service.work_round(current["round_id"])
    assert not service.run_slot.locked()


def test_default_model_settings_and_shared_savers(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline")
    llm = models.build_model(
        {"model": "openai/gpt-6-luna", "reasoning_effort": "medium"},
        parallel_tool_calls=False,
    )
    assert llm.model_kwargs["parallel_tool_calls"] is False
    assert llm.reasoning_effort == "medium"
    pool = object()
    monkeypatch.setattr(checkpoints.state, "pool", pool)
    made = []

    def saver(shared):
        assert shared is pool
        value = InMemorySaver()
        made.append(value)
        return value

    monkeypatch.setattr(checkpoints, "PostgresSaver", saver)
    assert checkpoints.new_saver() is not checkpoints.new_saver()
    assert len(made) == 2


def test_nested_dispatch_has_fresh_context_and_claim_traces_carry_both_ids(
    client, monkeypatch
):
    current = new_round(client)
    inherited = ContextVar("improver_test_context", default="fresh")
    inherited.set("improver-parent")
    seen = []

    def dispatch(identity, concurrency):
        seen.append(inherited.get())
        run = api.get_run(identity)
        metadata = claim_config(
            {"claim_id": run["members"][0]["claim_id"], "thread_id": "claim-thread"},
            "Work claim",
            identity,
        ).get("metadata", {})
        assert metadata["langfuse_tags"] == [identity, current["round_id"]]
        assert metadata["langfuse_session_id"].endswith(":claim-thread")

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    monkeypatch.setattr(checkpoints, "clear_threads", lambda threads: None)
    result = run_execution.execute(
        configuration(
            kind="evaluation",
            set_name="evaluation",
            tiny=False,
            round_id=current["round_id"],
        )
    )
    assert seen == ["fresh"]
    assert result["status"] == "finished"


@pytest.mark.parametrize("stop", [False, True])
def test_stop_or_limit_refuses_new_claims_and_holds_slot_until_drain(
    client, monkeypatch, stop
):
    current = new_round(client)
    run = api.start_run(
        configuration(
            kind="evaluation",
            set_name="evaluation",
            tiny=False,
            round_id=current["round_id"],
        )
    )
    first, second = [member["claim_id"] for member in run["members"][:2]]
    entered, drain = Event(), Event()

    def working(*args):
        api.admit_claim(first, run["run_id"])
        api.start_claim(first, "in-flight")
        entered.set()
        assert drain.wait(10)
        with pytest.raises(Refused):
            api.admit_claim(second, run["run_id"])
        assert api.finish_run(run["run_id"])["status"] == "stopped"

    monkeypatch.setattr(improver, "work", working)
    service.reserve("improvement round")
    with ThreadPoolExecutor(max_workers=1) as workers:
        active = workers.submit(service.work_round, current["round_id"])
        try:
            assert entered.wait(10)
            if stop:
                service.stop_round(
                    current["round_id"], service.Handler(**client.get("/api/me").json())
                )
                assert (
                    client.post(
                        f"/api/claims/{first}/decision",
                        json={
                            "decision": "decline",
                            "payout": 0,
                            "section": "Evidence",
                            "reason": "Late",
                            "decided_by": "Test handler",
                            "thread_id": api.get_claim(first)["thread_id"],
                        },
                    ).status_code
                    == 409
                )
            else:
                api.record_model_call(
                    first,
                    {
                        "reply_id": "spent-limit",
                        "run_id": run["run_id"],
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                        "cost": 0.5,
                    },
                )
            with pytest.raises(Refused):
                improver_api.admit(current["round_id"])
            with pytest.raises(Refused):
                api.start_claim(second, "must-not-start")
            assert service.run_slot.locked()
            with pytest.raises(Refused, match="improvement round"):
                service.start_dispatch(
                    service.DispatchRequest(concurrency=1), BackgroundTasks()
                )
        finally:
            drain.set()
        active.result()
    assert not service.run_slot.locked()
    assert api.get_claim(second)["attempts"] == 0
    assert api.get_current_configuration()["version"] == 1
