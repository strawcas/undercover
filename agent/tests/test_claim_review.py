import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import api, dispatcher, harness
from agent.tests.queue_setup import leave_waiting
from agent.tests.records import claim_of
from agent.tests.runtime_support import invoke, runtime_client, wait_for_idle
from agent.tests.stubs import calculate, call, graph_on, record, stub
from backend import claim_reviews, main
from backend.tests.run_requests import configuration

CLAIM = "CLM-1002"
NOTE = "Check the clinic charge against section 7."


def send_back(client, note=NOTE):
    return (
        client.post(f"/api/claims/{CLAIM}/send-back", json={"note": note})
        .raise_for_status()
        .json()
    )


def use_graph(monkeypatch, graph):
    monkeypatch.setattr(
        harness, "execution_graph", lambda version=None, **options: graph
    )


def resume():
    agent = runtime_client()
    response = invoke(agent, "resume_claim", claim_id=CLAIM)
    wait_for_idle(agent)
    return response.json()


@pytest.mark.usefixtures("runtime_defaults")
def test_repeated_reviews_keep_the_screen_and_reset_model_calls(client, monkeypatch):
    saver = InMemorySaver()
    original = stub(
        *(call("get_claim") for _ in range(28)),
        calculate(1350),
        record("approve", 1350),
    )
    harness.work_claim(CLAIM, graph_on(original, saver))
    first = send_back(client)
    assert first["payment"] is None
    replacement = stub(calculate(1250), record("approve", 1250))
    graph = harness.claim_graph(
        replacement,
        saver,
        {"version": 2, "prompt": "A decision can't be changed afterwards."},
    )
    use_graph(monkeypatch, graph)
    assert resume() == {"accepted": True}
    claim = claim_of(client, CLAIM)
    assert claim["status"] == "waiting_for_sign_off"
    assert claim["decision"]["payout"] == 1250
    assert claim["payment"] is None
    assert graph.get_state(harness.thread_of(claim)).values["model_calls"] == 2
    [review] = [
        message.text
        for message in replacement.prompts[0]
        if isinstance(message, HumanMessage) and NOTE in message.text
    ]
    assert "Senior handler Dana Whitfield" in review
    assert "policy wording and claims manual" in review
    assert "Refer the claim to a handler" in review
    prompt = replacement.prompts[0][0].text
    assert "A decision can't be changed afterwards." in prompt
    assert "senior review takes precedence" in prompt

    send_back(client, "Check the remaining receipt against section 7.")
    use_graph(
        monkeypatch, graph_on(stub(calculate(420), record("approve", 420)), saver)
    )
    assert resume() == {"accepted": True}
    paid = claim_of(client, CLAIM)
    assert paid["payment"]["amount"] == 420
    assert paid["thread_id"] == first["thread_id"]
    assert [item["decision"]["payout"] for item in paid["returned_decisions"]] == [
        1350,
        1250,
    ]
    timeline = client.get(f"/api/claims/{CLAIM}/steps").json()
    assert sum(step["kind"] == "security" for step in timeline) == 1


@pytest.mark.parametrize("taken", [False, True])
@pytest.mark.usefixtures("runtime_defaults")
def test_dispatch_recovers_a_missed_handoff_or_an_expired_review(
    client, monkeypatch, taken
):
    leave_waiting(CLAIM)
    saver = InMemorySaver()
    harness.work_claim(
        CLAIM, graph_on(stub(calculate(1350), record("approve", 1350)), saver)
    )
    first = send_back(client)
    timestamp = ["2026-11-10T09:00:00+00:00"]
    monkeypatch.setattr(claim_reviews, "now", lambda: timestamp[0])
    monkeypatch.setattr(main, "now", lambda: timestamp[0])
    run = api.start_run(configuration(concurrency=1))
    if taken:
        api.take_review(CLAIM, "lost")
        assert "refused" in resume()
        selected = dispatcher.list_work(
            {
                "run_id": run["run_id"],
                "claims": [],
                "results": [],
                "configuration_version": 1,
            }
        )
        assert selected["claims"] == []
    timestamp[0] = "2026-11-10T09:05:00+00:00"
    reply = record("refer", 0)
    reply.tool_calls[0]["args"]["reason"] = (
        "The requested change has no support in the policy. Review the receipt."
    )
    use_graph(monkeypatch, graph_on(stub(reply), saver))
    assert dispatcher.dispatch(run["run_id"], 1)["results"] == [CLAIM]
    resolved = claim_of(client, CLAIM)
    assert resolved["thread_id"] == first["thread_id"]
    assert resolved["decision"]["decision"] == "refer"
    assert resolved["decision"]["reason"] == reply.tool_calls[0]["args"]["reason"]
    assert resolved["payment"] is None


def test_dispatch_resumes_after_the_review_was_saved_and_worker_was_lost(
    client, monkeypatch
):
    leave_waiting(CLAIM)
    saver = InMemorySaver()
    harness.work_claim(
        CLAIM, graph_on(stub(calculate(1350), record("approve", 1350)), saver)
    )
    send_back(client)
    timestamp = ["2026-11-10T09:00:00+00:00"]
    monkeypatch.setattr(claim_reviews, "now", lambda: timestamp[0])
    monkeypatch.setattr(main, "now", lambda: timestamp[0])
    taken = api.take_review(CLAIM, "lost-after-resume")
    graph = graph_on(stub(KeyboardInterrupt()), saver)
    with pytest.raises(KeyboardInterrupt):
        harness.carry_on(taken, graph)
    checkpoint = graph.get_state(harness.thread_of(taken))
    assert checkpoint.values["reviews_applied"] == 1
    assert not checkpoint.interrupts
    timestamp[0] = "2026-11-10T09:05:00+00:00"
    replacement = stub(record("refer", 0))
    use_graph(monkeypatch, graph_on(replacement, saver))
    run = api.start_run(configuration(concurrency=1))
    dispatcher.dispatch(run["run_id"], 1)
    assert claim_of(client, CLAIM)["decision"]["decision"] == "refer"
    assert (
        sum(
            isinstance(message, HumanMessage) and NOTE in message.text
            for message in replacement.prompts[0]
        )
        == 1
    )


def test_dispatch_recovers_if_the_old_tool_saved_only_to_the_backend(
    client, monkeypatch
):
    leave_waiting(CLAIM)
    saver = InMemorySaver()
    graph = graph_on(stub(calculate(1350), record("approve", 1350)), saver)
    submit = api.record_decision

    def lose_worker(*args, **kwargs):
        submit(*args, **kwargs)
        raise KeyboardInterrupt

    with monkeypatch.context() as before:
        before.setattr(api, "record_decision", lose_worker)
        with pytest.raises(KeyboardInterrupt):
            harness.work_claim(CLAIM, graph)
    returned = send_back(client)
    checkpoint = graph.get_state(harness.thread_of(returned))
    assert not checkpoint.interrupts
    assert checkpoint.next == (harness.TOOLS,)
    use_graph(monkeypatch, graph_on(stub(record("refer", 0)), saver))
    run = api.start_run(configuration(concurrency=1))
    dispatcher.dispatch(run["run_id"], 1)
    resolved = claim_of(client, CLAIM)
    assert resolved["thread_id"] == returned["thread_id"]
    assert resolved["decision"]["decision"] == "refer"
