"""The agent stopped partway, by a crash, a restart or Ctrl-C, and carrying on. The
tests stop it with KeyboardInterrupt, which the harness doesn't catch, so the claim is
left as a crash leaves it."""

import itertools

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import update

from agent import api, harness
from agent.tests.records import claim_of, steps_of
from agent.tests.stubs import StubModel, calculate, call, decide, graph_on, record, stub
from backend.database import claims, decisions, engine


def stop_while_working(claim_id, saver, *replies):
    """Work a claim until the agent is stopped after the replies given."""
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim(
            claim_id, graph_on(stub(*replies, KeyboardInterrupt()), saver)
        )


def fail_once(monkeypatch, module, name, error, carry_out=False):
    """Make the next call to a function raise an error, once it has been carried out
    if asked, as a crash or a lost connection at that moment would."""
    function = getattr(module, name)

    def then_fail(*args, **kwargs):
        monkeypatch.setattr(module, name, function)
        if carry_out:
            function(*args, **kwargs)
        raise error

    monkeypatch.setattr(module, name, then_fail)


def calls_to(monkeypatch, name):
    """Count the calls to one of the backend's operations."""
    calls = []
    function = getattr(api, name)

    def count(*args, **kwargs):
        calls.append(args)
        return function(*args, **kwargs)

    monkeypatch.setattr(api, name, count)
    return calls


class StopBeforeCheckpoint(InMemorySaver):
    """Saves a step's writes, then stops before saving the checkpoint that follows,
    once a step has recorded a decision: the gap a crash can land in."""

    stopping = False

    def put_writes(self, config, writes, task_id, task_path=""):
        super().put_writes(config, writes, task_id, task_path)
        self.stopping = self.stopping or any(c == "decision" for c, _ in writes)

    def put(self, config, checkpoint, metadata, new_versions):
        if self.stopping:
            self.stopping = False
            raise KeyboardInterrupt
        return super().put(config, checkpoint, metadata, new_versions)


def test_a_claim_stopped_partway_carries_on_from_its_last_step(client):
    saver = InMemorySaver()
    stop_while_working("CLM-1001", saver, call("get_claim"), call("get_policy"))
    stopped = claim_of(client, "CLM-1001")
    assert (stopped["status"], stopped["attempts"]) == ("in_progress", 1)
    before = steps_of(client, "CLM-1001")

    model = stub(calculate(420.0), record("approve", 420.0))
    report = harness.carry_on(api.get_claim("CLM-1001"), graph_on(model, saver))
    assert report == "CLM-1001  approve  $420.00"
    claim = claim_of(client, "CLM-1001")
    assert (claim["thread_id"], claim["attempts"]) == (stopped["thread_id"], 1)
    assert claim["payment"]["amount"] == 420.0
    first = model.prompts[0]
    assert len([m for m in first if isinstance(m, HumanMessage)]) == 1
    earlier = [m.name for m in first if isinstance(m, ToolMessage)]
    assert earlier == ["get_claim", "get_policy"]
    steps = steps_of(client, "CLM-1001")
    assert steps[: len(before)] == before
    assert len(steps) == len(before) + 4
    assert {s["thread_id"] for s in steps} == {claim["thread_id"]}
    assert "error" not in {s["kind"] for s in steps}


def test_stopped_just_after_the_decision_is_recorded(client, monkeypatch):
    saver = InMemorySaver()
    fail_once(monkeypatch, api, "record_decision", KeyboardInterrupt(), carry_out=True)
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim("CLM-1001", graph_on(stub(*decide("approve", 420.0)), saver))
    assert claim_of(client, "CLM-1001")["payment"] is None

    model = stub()
    report = harness.carry_on(api.get_claim("CLM-1001"), graph_on(model, saver))
    assert report == "CLM-1001  approve  $420.00"
    assert claim_of(client, "CLM-1001")["payment"]["amount"] == 420.0
    assert model.prompts == []


def test_stopped_just_after_the_payment_is_recorded(client, monkeypatch):
    saver = InMemorySaver()
    fail_once(monkeypatch, api, "pay_claim", KeyboardInterrupt(), carry_out=True)
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim("CLM-1001", graph_on(stub(*decide("approve", 420.0)), saver))
    paid = claim_of(client, "CLM-1001")["payment"]

    payments = calls_to(monkeypatch, "pay_claim")
    graph = graph_on(stub(), saver)
    report = harness.carry_on(api.get_claim("CLM-1001"), graph)
    assert report == "CLM-1001  approve  $420.00"
    assert payments == [("CLM-1001", 420.0)]
    assert claim_of(client, "CLM-1001")["payment"] == paid
    thread = harness.thread_of(claim_of(client, "CLM-1001"))
    assert graph.get_state(thread).next == ()


def test_stopped_after_a_step_was_written_but_before_its_checkpoint(client):
    saver = StopBeforeCheckpoint()
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim("CLM-1001", graph_on(stub(*decide("approve", 420.0)), saver))
    assert claim_of(client, "CLM-1001")["decision"]["payout"] == 420.0

    model = stub()
    graph = graph_on(model, saver)
    report = harness.carry_on(api.get_claim("CLM-1001"), graph)
    assert report == "CLM-1001  approve  $420.00"
    assert model.prompts == []
    claim = claim_of(client, "CLM-1001")
    assert (claim["payment"]["amount"], claim["attempts"]) == (420.0, 1)
    thread = harness.thread_of(claim)
    assert graph.get_state(thread).values["decision"]["payout"] == 420.0


def test_stopped_on_the_way_into_the_pause_is_paid_after_the_sign_off(
    client, monkeypatch
):
    saver = InMemorySaver()
    fail_once(monkeypatch, harness, "interrupt", KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim(
            "CLM-1002", graph_on(stub(*decide("approve", 1350.0)), saver)
        )
    client.post("/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"})

    report = harness.carry_on(api.get_claim("CLM-1002"), graph_on(stub(), saver))
    assert report == "CLM-1002  approve  $1,350.00"
    assert claim_of(client, "CLM-1002")["payment"]["signed_off_by"] == "Dana Whitfield"


@pytest.mark.parametrize("after_write", [False, True])
def test_signed_off_decision_resumes_before_tool_checkpoint(
    client, monkeypatch, after_write
):
    saver = StopBeforeCheckpoint() if after_write else InMemorySaver()
    if not after_write:
        fail_once(
            monkeypatch, api, "record_decision", KeyboardInterrupt(), carry_out=True
        )
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim(
            "CLM-1002", graph_on(stub(calculate(1350), record("approve", 1350)), saver)
        )
    before = claim_of(client, "CLM-1002")
    client.post("/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"})
    graph = graph_on(stub(), saver)
    assert "approve" in harness.carry_on(api.get_claim("CLM-1002"), graph)
    claim = claim_of(client, "CLM-1002")
    assert claim["payment"]["amount"] == 1350
    assert claim["thread_id"] == before["thread_id"]
    assert claim["attempts"] == 1
    assert graph.get_state(harness.thread_of(claim)).next == ()


def test_finished_without_a_decision_but_stopped_before_it_was_failed(
    client, monkeypatch
):
    saver = InMemorySaver()
    fail_once(monkeypatch, harness, "settle", KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim("CLM-1004", graph_on(stub(call("get_claim"), "Hmm."), saver))
    assert claim_of(client, "CLM-1004")["status"] == "in_progress"

    model = stub()
    report = harness.carry_on(api.get_claim("CLM-1004"), graph_on(model, saver))
    assert report == (
        "CLM-1004  failed: The agent finished without recording a decision,"
        " back in the queue"
    )
    assert model.prompts == []


def test_stale_payment_node_uses_the_backend_paid_outcome(client, monkeypatch):
    saver = InMemorySaver()
    claim_id = "CLM-1001"
    fail_once(monkeypatch, api, "pay_claim", KeyboardInterrupt())
    graph = graph_on(stub(calculate(900), record("approve", 900)), saver)
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim(claim_id, graph)
    claim = api.get_claim(claim_id)
    assert graph.get_state(harness.thread_of(claim)).next == (harness.PAY,)
    values = client.get("/api/settings").json()["values"]
    client.put(
        "/api/settings", json=values | {"payout_approval_limit": 500}
    ).raise_for_status()
    client.post(f"/api/claims/{claim_id}/sign-off").raise_for_status()
    paid = api.get_claim(claim_id)
    calls = calls_to(monkeypatch, "pay_claim")
    assert "approve" in harness.carry_on(paid, graph_on(stub(), saver))
    assert calls == []
    assert paid["payment"]["amount"] == 900
    assert graph.get_state(harness.thread_of(paid)).values["status"] == "decided"


def test_legacy_signature_keeps_the_pause_until_the_backend_pays(client, monkeypatch):
    claim_id = "CLM-1002"
    saver = InMemorySaver()
    graph = graph_on(stub(calculate(1350), record("approve", 1350)), saver)
    assert "waiting for sign-off" in harness.work_claim(claim_id, graph)
    with engine().begin() as db:
        db.execute(
            update(claims)
            .where(claims.c.claim_id == claim_id)
            .values(status="in_progress")
        )
        db.execute(
            update(decisions)
            .where(decisions.c.claim_id == claim_id)
            .values(
                signed_off_by="Dana Whitfield",
                signed_off_at="2026-07-20T09:00:00+00:00",
            )
        )
    unpaid = api.get_claim(claim_id)
    thread = harness.thread_of(unpaid)
    calls = calls_to(monkeypatch, "pay_claim")
    assert "not paid yet" in harness.carry_on(unpaid, graph)
    assert graph.get_state(thread).next == (harness.PAUSE,)
    paid = client.post(f"/api/claims/{claim_id}/sign-off").json()
    assert harness.carry_on(paid, graph) == f"{claim_id}  approve  $1,350.00"
    assert calls == []
    checkpoint = graph.get_state(thread)
    assert checkpoint.next == ()
    assert checkpoint.values["status"] == "decided"


def test_a_claim_stopped_before_its_first_step_was_saved(client):
    client.post("/api/claims/CLM-1001/start", json={"thread_id": "nothing-saved"})
    model = stub(*decide("refer", 0))
    report = harness.carry_on(api.get_claim("CLM-1001"), graph_on(model))
    assert report == "CLM-1001  refer  $0.00"
    claim = claim_of(client, "CLM-1001")
    assert (claim["thread_id"], claim["attempts"]) == ("nothing-saved", 1)
    assert model.prompts[0][1].content == "Claim CLM-1001"


def test_the_model_call_limit_holds_across_a_restart(client):
    saver = InMemorySaver()
    before = 20
    stop_while_working("CLM-1004", saver, *(call("get_claim") for _ in range(before)))
    endless = StubModel(messages=(call("get_claim") for _ in itertools.count()))
    report = harness.carry_on(api.get_claim("CLM-1004"), graph_on(endless, saver))
    assert report.startswith("CLM-1004  failed: TooManyModelCalls: ")
    assert len(endless.prompts) == harness.CLAIM_MODEL_CALLS - before


def test_a_claim_that_fails_while_carried_on_starts_afresh_next_time(client):
    saver = InMemorySaver()
    stop_while_working("CLM-1004", saver, call("get_claim"))
    stopped = claim_of(client, "CLM-1004")
    outage = stub(RuntimeError("503: The model is unavailable"))
    report = harness.carry_on(api.get_claim("CLM-1004"), graph_on(outage, saver))
    assert report == (
        "CLM-1004  failed: RuntimeError: 503: The model is unavailable,"
        " back in the queue"
    )
    assert len(outage.prompts) == 1

    harness.work_claim("CLM-1004", graph_on(stub(*decide("refer", 0)), saver))
    claim = claim_of(client, "CLM-1004")
    assert claim["attempts"] == 2
    assert claim["thread_id"] != stopped["thread_id"]
