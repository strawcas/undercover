import json
from uuid import uuid4

import httpx
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.copilot import actions, api, criteria, graph, reads, worker
from agent.copilot.state import Context
from agent.tests.stubs import call, stub

BEARER = "copilot-test-sentinel"


def working_context(name="Dana Whitfield", senior=True):
    receipt = api.Progress(api.HandlerClient(BEARER), *(str(uuid4()) for _ in range(3)))
    return Context(
        receipt,
        {"name": name, "can_approve_over_limit": senior, "handler_id": "HND-01"},
    )


def start_state(question):
    return {
        "messages": [HumanMessage(question)],
        "model": "stub",
        "calls": 0,
        "cost": 0.0,
    }


def install_app(monkeypatch, handle):
    def reply(request):
        assert request.headers["Authorization"].startswith("Bearer ")
        assert "X-API-Key" not in request.headers
        return handle(request)

    monkeypatch.setattr(
        api,
        "backend",
        httpx.Client(
            base_url="http://app/api",
            transport=httpx.MockTransport(reply),
        ),
    )


def test_graph_keeps_only_public_results_and_carries_history(monkeypatch):
    cards = []

    def reply(request):
        if request.method == "PUT":
            cards.append(json.loads(request.content))
            return httpx.Response(204)
        assert request.url.path == "/api/claims"
        return httpx.Response(200, json=[{"claim_id": "CLM-1001", "reflected": BEARER}])

    install_app(monkeypatch, reply)
    context = working_context()
    model = stub(call("list_claims"), "CLM-1001 is waiting.")
    saver = InMemorySaver()
    runner = graph.build(model, saver, reads.READ_TOOLS)
    config = graph.config(context.progress.turn_id)
    result = runner.invoke(start_state("What is waiting?"), config, context=context)
    assert result["calls"] == 2
    assert result["cost"] is None
    assert BEARER not in repr(model.prompts)
    assert BEARER not in repr(list(saver.list(config)))
    assert [card["status"] for card in cards] == ["working", "done"]
    assert BEARER not in repr(cards)
    rebuilt = graph.build(stub("Yes."), saver, reads.READ_TOOLS)
    assert (
        graph.earlier_messages(rebuilt, context.progress.turn_id)[-1].text
        == "CLM-1001 is waiting."
    )


def test_model_has_a_hard_call_limit(monkeypatch):
    install_app(
        monkeypatch,
        lambda request: httpx.Response(
            204 if request.method == "PUT" else 200,
            json=[],
        ),
    )
    context = working_context()
    model = stub(*(call("list_claims") for _ in range(25)))
    runner = graph.build(model, InMemorySaver(), reads.READ_TOOLS)
    result = runner.invoke(
        start_state("Keep reading"),
        graph.config(context.progress.turn_id),
        context=context,
    )
    assert result["calls"] == 25
    assert "call limit" in result["messages"][-1].text


def test_a_new_graph_can_resume_the_same_durable_confirmation(monkeypatch):
    paid = []
    card = {
        "claim_id": "CLM-1002",
        "customer_name": "Tom Becker",
        "revision": 2,
        "evaluation_id": None,
        "sign_off_available": True,
        "decision": {"payout": 1350, "reason": "Covered"},
    }

    def reply(request):
        if request.method == "PUT":
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(200, json=card)
        assert request.headers["If-Match"] == '"2"'
        assert request.headers["Authorization"] == "Bearer renewed-test-token"
        assert api.RECEIPT_HEADER in request.headers
        paid.append(request.url.path)
        return httpx.Response(200, json={"payment": {"amount": 1350}})

    install_app(monkeypatch, reply)
    saver = InMemorySaver()
    tools = [*reads.READ_TOOLS, *actions.ACTION_TOOLS]
    context = working_context()
    config = graph.config(context.progress.turn_id)
    model = stub(call("prepare_sign_off", claim_id="CLM-1002"))
    first = graph.build(model, saver, tools)
    result = first.invoke(
        start_state("Sign off"), config, context=context, durability="sync"
    )
    assert result["__interrupt__"][0].value["amount"] == 1350
    assert not paid
    fresh = api.Progress(
        api.HandlerClient("renewed-test-token"),
        context.progress.conversation_id,
        context.progress.turn_id,
        str(uuid4()),
    )
    rebuilt = graph.build(stub(), saver, tools)
    result = rebuilt.invoke(
        Command(resume=True), config, context=Context(fresh, context.handler)
    )
    assert result["messages"][-1].text == "I signed off CLM-1002 and paid $1,350.00."
    assert paid == ["/api/claims/CLM-1002/sign-off"]
    assert result["calls"] == 1


def test_ordinary_handler_gets_a_refusal_instead_of_an_interrupt(monkeypatch):
    statuses = []

    def reply(request):
        assert request.method == "PUT"
        statuses.append(json.loads(request.content)["status"])
        return httpx.Response(204)

    install_app(monkeypatch, reply)
    context = working_context("Marcus Oyelaran", senior=False)
    model = stub(
        call("prepare_sign_off", claim_id="CLM-1002"), "A senior handler is required."
    )
    runner = graph.build(model, InMemorySaver(), actions.ACTION_TOOLS)
    result = runner.invoke(
        start_state("Sign off"), graph.config(context.progress.turn_id), context=context
    )
    assert "__interrupt__" not in result
    assert statuses == ["working", "refused"]
    assert "senior handler" in model.prompts[1][-1].text


def test_invalid_bearer_stops_before_taking_work_or_building_graph(monkeypatch):
    def reject(request):
        assert request.url.path == "/api/me"
        return httpx.Response(401, json={"detail": "Expired"})

    install_app(monkeypatch, reject)
    monkeypatch.setattr(
        worker, "active_graph", lambda: (_ for _ in ()).throw(AssertionError())
    )
    worker.run(BEARER, *(str(uuid4()) for _ in range(3)))


def test_criteria_uses_saved_figures_and_checks_coverage_when_evaluation_fails():
    coverage = {"limit": 1000, "paid": 0, "signed_off": 0}
    failed = criteria.table(
        0.8, {"status": "stopped", "stop_reason": "Agent stopped"}, coverage
    )
    assert "No payouts above the limit" in failed
    assert "Unavailable" in failed
    assert "cannot yet verify" in failed
    assert "Agent stopped" in failed
    scores = {"straight_through": 0.71, "accuracy": 0.79}
    completed = criteria.table(0.8, {"status": "finished", "scores": scores}, coverage)
    assert "71.0% | Met" in completed
    assert "79.0% | Missed" in completed
    assert "$1,000.00" in completed
