import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agent import http as agent_http
from agent import main, queue
from agent.execution import Execution
from agent.main import AgentRunError
from agent.tests.queue_stubs import ScriptedQueueModel
from agent.tests.stubs import StubModel, api_client, scripted_model
from backend.claims import Claims


def test_agent_chooses_order_and_early_final_cannot_skip_work(
    client, repository, monkeypatch
):
    requests = []
    monkeypatch.setattr(queue, "backend", lambda: api_client(client, requests))
    monkeypatch.setattr(main, "backend", lambda: api_client(client, requests))
    order = ["CLM-1003", "CLM-1001", "CLM-1002"]
    planner = ScriptedQueueModel(order=order, finish_early=True)
    helpers = []

    def helper(claim_id, thread_id):
        llm = scripted_model()
        helpers.append((claim_id, thread_id, llm))
        return main.run(claim_id, thread_id, llm)

    result = queue.run_queue({"mode": "limited", "max_claims": 3}, helper, planner)
    assert result["admitted_claim_ids"] == order
    assert result["counts"]["worked"] == result["counts"]["simulated_payments"] == 3
    assert result["counts"]["waiting_remaining"] == 22
    assert {item["status"] for item in result["todos"]} == {"done"}
    assert planner.final_replies == 2
    assert len({thread for _, thread, _ in helpers}) == 3
    for claim_id, _, llm in helpers:
        assert len(llm.first_messages) == 2
        assert llm.first_messages[0].type == "system"
        assert llm.first_messages[1].content == f"Decide claim {claim_id}."
    parent_tool_names = {
        message.name
        for message in planner.last_messages
        if isinstance(message, ToolMessage)
    }
    assert parent_tool_names <= {
        "list_waiting_claims",
        "write_todos",
        "delegate_claim",
        "check_completion",
    }
    assert "read_reference_section" not in parent_tool_names
    paths = [path for _, path in requests]
    root = f"/api/agent-runs/{result['run_id']}"
    assert paths[0] == "/api/agent-runs"
    assert paths.index(f"{root}/todos") < paths.index(f"{root}/attempts")
    assert paths.index(f"{root}/attempts") < paths.index(
        "/api/claims/CLM-1003/decision"
    )
    assert paths[-1] == f"{root}/finish"
    assert any(path.endswith("/reference-reads") for path in paths)
    assert "/api/policy-wording" not in paths


def test_failed_claim_retries_after_other_work_within_the_cap(
    client, repository, monkeypatch
):
    monkeypatch.setattr(queue, "backend", lambda: api_client(client))
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    started, observed = [], []

    def helper(claim_id, thread_id):
        started.append(claim_id)
        if claim_id == "CLM-1001":
            raise RuntimeError("Sensitive provider body")
        return main.run(claim_id, thread_id, scripted_model())

    result = queue.run_queue(
        {"mode": "limited", "max_claims": 2},
        helper,
        execution=Execution(on_result=observed.append),
    )
    assert started == ["CLM-1001", "CLM-1002", "CLM-1001"]
    assert len(observed) == 3
    assert result["counts"]["worked"] == 2
    assert result["counts"]["assessment_attempts"] == 3
    assert result["counts"]["handler_review"] == 1
    assert result["todos"][0]["handoff_reason"] == "technical_failure"
    assert Claims(repository).detail("CLM-1002")["payment"]["amount"] == "420.00"
    assert Claims(repository).detail("CLM-1003")["status"] == "waiting"
    assert "Sensitive" not in str(result)


def test_completion_guard_has_a_finite_coordinator_budget(
    client, repository, monkeypatch
):
    monkeypatch.setattr(queue, "backend", lambda: api_client(client))
    planner = StubModel(
        messages=(AIMessage(content="All work is complete.") for _ in range(30))
    )
    context = Execution()
    with pytest.raises(AgentRunError, match="ModelCallLimitExceededError"):
        queue.run_queue(
            {"mode": "claim", "claim_id": "CLM-1001"}, llm=planner, execution=context
        )
    assert context.result is not None
    assert context.result["status"] == "failed"
    assert context.result["counts"]["assessment_attempts"] == 0
    assert Claims(repository).detail("CLM-1001")["status"] == "waiting"


def test_empty_queue_calls_no_model_and_needs_no_key(client, repository, monkeypatch):
    monkeypatch.setattr(queue, "backend", lambda: api_client(client))
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with repository.transaction() as connection:
        connection.execute("UPDATE claims SET state='handler_review'")
    planner = ScriptedQueueModel()
    result = queue.run_queue({"mode": "all"}, llm=planner)
    assert result["status"] == "completed"
    assert result["claim_limit"] == result["counts"]["worked"] == 0
    assert planner.calls == []


def test_lost_reservation_response_starts_one_helper(client, repository, monkeypatch):
    requests, faults, delays, helpers = [], [], [], []

    def lose_response(request, _response):
        if request.url.path.endswith("/attempts") and not faults:
            faults.append(True)
            raise httpx.ReadTimeout("Synthetic lost response", request=request)

    monkeypatch.setattr(
        queue, "backend", lambda: api_client(client, requests, lose_response)
    )
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    monkeypatch.setattr(agent_http, "sleep", delays.append)

    def helper(claim_id, thread_id):
        helpers.append(thread_id)
        return main.run(claim_id, thread_id, scripted_model())

    result = queue.run_queue({"mode": "claim", "claim_id": "CLM-1001"}, helper)
    assert len(helpers) == 1
    assert result["counts"]["assessment_attempts"] == 1
    assert len([path for _, path in requests if path.endswith("/attempts")]) == 2
    assert delays == [0.25]
    assert Claims(repository).detail("CLM-1001")["payment"]["amount"] == "420.00"


@pytest.mark.parametrize("provider", ["openrouter", "ollama"])
def test_queue_and_helper_share_model_configuration(client, monkeypatch, provider):
    monkeypatch.setattr(queue, "backend", lambda: api_client(client))
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    monkeypatch.setenv("MODEL_NAME", "test-model")
    if provider == "ollama":
        monkeypatch.setenv("OPENROUTER_API_KEY", "")
    seen = []

    def configured(**settings):
        seen.append(settings)
        return ScriptedQueueModel() if len(seen) == 1 else scripted_model()

    factory = "ChatOllama" if provider == "ollama" else "ChatOpenAI"
    monkeypatch.setattr(main, factory, configured)
    monkeypatch.setattr(queue, "model", main.model)
    result = queue.run_queue({"mode": "claim", "claim_id": "CLM-1001"})
    assert result["counts"]["simulated_payments"] == 1
    assert [settings["model"] for settings in seen] == ["test-model", "test-model"]
    assert seen[0] == seen[1]
