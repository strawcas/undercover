from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from agent import main
from agent.checkpoints import checkpointer
from agent.execution import AgentRunError
from agent.graph import ClaimState, ClaimWorkflow, build_graph
from agent.tests.stubs import StubModel, api_client, scripted_model
from agent.tools import ClaimTools
from backend.claims import Claims
from backend.tests.conftest import begin_claim


def initial(claim_id, thread, instructions):
    return ClaimState(
        messages=[HumanMessage(content=f"Decide claim {claim_id}.")],
        claim_id=claim_id,
        thread_id=thread,
        run_id="",
        attempt_number=1,
        instructions=instructions,
        decision=None,
        saved_claim=None,
        model_calls=0,
    )


def test_saved_interrupt_resumes_without_model_or_new_attempt(
    client, repository, monkeypatch
):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    thread = begin_claim(repository)
    claims = Claims(repository)
    main.run("CLM-1001", thread, scripted_model(payout="1250.00"))
    assert claims.detail("CLM-1001")["status"] == "awaiting_sign_off"
    assert claims.detail("CLM-1001")["payment"] is None
    config: RunnableConfig = {"configurable": {"thread_id": thread}}
    with checkpointer() as saver:
        graph = build_graph(
            ClaimWorkflow(
                ClaimTools(api_client(client), "CLM-1001", thread), scripted_model()
            ),
            saver,
        )
        assert graph.get_state(config).next == ("approval",)
    claims.sign_off("CLM-1001", "HND-01")

    def unavailable_model():
        raise AssertionError("Payment-only continuation must not construct a model")

    monkeypatch.setattr(main, "model", unavailable_model)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    main.run("CLM-1001", thread)
    assert claims.detail("CLM-1001")["payment"]["amount"] == "1250.00"
    assert (
        len([e for e in claims.events("CLM-1001") if e["action"] == "Record decision"])
        == 1
    )
    assert len(client.get("/api/agent-runs/current").json()["run"]["attempts"]) == 1


def test_checkpoint_reopen_continues_after_saved_tools(client, repository, monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    thread = begin_claim(repository)
    instructions = client.get("/api/agent-instructions").json()["text"]
    config: RunnableConfig = {"configurable": {"thread_id": thread}}
    with api_client(client) as transport, checkpointer() as saver:
        workflow = ClaimWorkflow(
            ClaimTools(transport, "CLM-1001", thread), scripted_model()
        )
        graph = build_graph(workflow, saver)
        graph.invoke(
            initial("CLM-1001", thread, instructions),
            config,
            durability="sync",
            interrupt_after=["tools"],
        )
        assert graph.get_state(config).next == ("agent",)
    continuation = StubModel(messages=iter(list(scripted_model().messages)[1:]))
    main.run("CLM-1001", thread, continuation)
    assert len(continuation.first_messages) > 2
    detail = Claims(repository).detail("CLM-1001")
    assert detail["payment"]["amount"] == "420.00"
    assert detail["failed_attempts"] == 0
    assert (
        client.get("/api/agent-runs/current").json()["run"]["attempts"][0][
            "attempt_number"
        ]
        == 1
    )


def test_backend_refusal_is_returned_to_reasoning(client, repository, monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    replies = list(scripted_model().messages)
    llm = StubModel(
        messages=iter(
            [
                AIMessage(content="Ready to decide without reading."),
                replies[-1],
                *replies,
            ]
        )
    )
    thread = begin_claim(repository)
    main.run("CLM-1001", thread, llm)
    events = Claims(repository).events("CLM-1001")
    assert any(
        e["action"] == "Save decision" and e["status"] == "blocked" for e in events
    )
    assert sum(e["action"] == "Record decision" for e in events) == 1
    assert Claims(repository).detail("CLM-1001")["failed_attempts"] == 0


def test_model_call_budget_is_durable_and_request_replay_is_safe(client, repository):
    thread = begin_claim(repository)
    path = "/api/claims/CLM-1001/usage"
    body = dict(thread_id=thread, request_id=str(uuid4()), kind="model", limit=80)
    for _ in range(30):
        body = dict(thread_id=thread, request_id=str(uuid4()), kind="model", limit=80)
        response = client.post(path, json=body)
        assert response.status_code == 200
    assert client.post(path, json=body).json()["model_calls"] == 30
    assert client.post(path, json=body | {"kind": "tool"}).status_code == 409
    assert (
        client.post(path, json=body | {"request_id": str(uuid4())}).status_code == 409
    )
    with repository.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM attempt_usage WHERE thread_id=?", (thread,)
            ).fetchone()[0]
            == 30
        )


def test_no_model_payment_or_sign_off_tool(client, repository):
    with api_client(client) as transport:
        names = {
            tool.name
            for tool in ClaimTools(
                transport, "CLM-1001", begin_claim(repository)
            ).tools()
        }
    assert not {"record_decision", "pay_claim", "sign_off_claim"} & names
    assert "calculate_payout" in names


def test_structured_decision_is_required_input(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]["UsageInput"]
    assert set(schema["properties"]) == set(schema["required"])


def test_graph_cannot_reset_an_exhausted_model_budget(client, repository, monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    thread = begin_claim(repository)
    messages = (
        AIMessage(
            content="",
            tool_calls=[{"name": "read_claim", "args": {}, "id": str(uuid4())}],
        )
        for _ in range(40)
    )
    with pytest.raises(AgentRunError, match="model-call limit"):
        main.run("CLM-1001", thread, StubModel(messages=messages))
    unused = StubModel(messages=iter(()))
    with pytest.raises(AgentRunError, match="model-call limit"):
        main.run("CLM-1001", thread, unused)
    assert unused.first_messages == []
    with repository.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM attempt_usage WHERE thread_id=? AND kind='model'",
                (thread,),
            ).fetchone()[0]
            == 30
        )
