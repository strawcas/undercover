from datetime import UTC, datetime, timedelta
from threading import Event

import httpx
import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from ollama import ResponseError

from agent import http as agent_http
from agent import main, queue
from agent.execution import AgentRunError, AttemptBudget, Execution, QueueStopped
from agent.financial import write
from agent.invocation import invoke_helper
from agent.middleware import ProviderRetry
from agent.tests.queue_stubs import ScriptedQueueModel
from agent.tests.stubs import api_client, scripted_model
from agent.tools import ClaimTools
from backend.claims import Claims
from backend.harness_models import Attempt
from backend.runs import Runs
from backend.tests.conftest import begin_claim, begin_decision_claim, decision_payload
from backend.worker import reconcile_execution


@pytest.mark.parametrize("suffix", ["/decision", "/agent-pay"])
def test_lost_financial_response_is_read_back_without_replay(
    client, repository, monkeypatch, suffix
):
    seen, faults = [], []

    def lose(request, _response):
        if request.url.path.endswith(suffix) and not faults:
            faults.append(True)
            raise httpx.ReadTimeout("Lost after commit", request=request)

    monkeypatch.setattr(main, "backend", lambda: api_client(client, seen, lose))
    thread = begin_claim(repository)
    main.run("CLM-1001", thread, scripted_model())
    Claims(repository).finish("CLM-1001", thread)
    assert sum(path.endswith(suffix) for _, path in seen) == 1
    claim = Claims(repository).detail("CLM-1001")
    assert claim["payment"]["amount"] == "420.00"
    assert claim["failed_attempts"] == 0


def test_safe_read_retries_but_validation_does_not(client, repository, monkeypatch):
    thread = begin_claim(repository)
    delays, faults, seen = [], [], []

    def lose(request, _response):
        if request.method == "GET" and len(faults) < 2:
            faults.append(True)
            raise httpx.ConnectError("Synthetic", request=request)

    monkeypatch.setattr(agent_http, "sleep", delays.append)
    with api_client(client, seen, lose) as transport:
        tools = ClaimTools(transport, "CLM-1001", thread)
        assert tools.call("Read", "GET", tools.path)["claim_id"] == "CLM-1001"
        assert tools.call("Pay", "POST", tools.path + "/agent-pay", {})["status"] == 422
    assert delays == [0.25, 0.5]
    assert sum(method == "GET" for method, _ in seen) == 3
    assert sum(path.endswith("/agent-pay") for _, path in seen) == 1
    assert "Transport retry 2" in str(Claims(repository).events("CLM-1001"))


@pytest.mark.parametrize("status", [400, 429, 503])
def test_provider_retry_budget_and_backoff(monkeypatch, status):
    delays, attempts, retries = [], [], []
    monkeypatch.setattr(
        "langchain.agents.middleware.model_retry.time.sleep", delays.append
    )
    middleware = ProviderRetry(retries.append)

    def fail(_request):
        attempts.append(True)
        raise ResponseError("Sensitive provider details", status_code=status)

    request = ModelRequest(
        model=scripted_model(), messages=[], state={"messages": []}, runtime=None
    )
    with pytest.raises(ResponseError):
        middleware.wrap_model_call(request, fail)
    assert len(attempts) == (1 if status == 400 else 3)
    assert delays == ([] if status == 400 else [0.25, 0.5])
    assert retries == ([] if status == 400 else [1, 2])


def test_provider_retry_can_recover(monkeypatch):
    monkeypatch.setattr(
        "langchain.agents.middleware.model_retry.time.sleep", lambda _delay: None
    )
    calls = []

    def recover(_request):
        calls.append(True)
        if len(calls) == 1:
            raise httpx.ConnectError("Synthetic")
        return ModelResponse(result=[AIMessage(content="Recovered")])

    request = ModelRequest(
        model=scripted_model(), messages=[], state={"messages": []}, runtime=None
    )
    result = ProviderRetry().wrap_model_call(request, recover)
    assert isinstance(result, ModelResponse)
    assert result.result[0].content == "Recovered"
    assert len(calls) == 2


def test_helper_deadline_and_cancellation_do_not_wait_for_a_hung_provider(repository):
    release = Event()
    thread = begin_claim(repository)
    current = Runs(repository).current()
    assert current is not None
    entry = Attempt.model_validate(current["attempts"][0])
    assert entry.thread_id == thread
    entry.deadline_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    try:
        with pytest.raises(AgentRunError, match="deadline"):
            invoke_helper(
                lambda _c, _t, *, budget: str(release.wait(2)), entry, Execution()
            )
        context = Execution()
        context.cancel_active.set()
        entry.deadline_at = (datetime.now(UTC) + timedelta(seconds=10)).isoformat()
        with pytest.raises(QueueStopped):
            invoke_helper(lambda _c, _t, *, budget: "", entry, context)
    finally:
        release.set()


def test_http_cleanup_failure_is_reconciled_before_ownership_release(
    client, repository, monkeypatch
):
    thread = begin_claim(repository)
    claim = Claims(repository).detail("CLM-1001")
    context = Execution(run_id=claim["run_id"])
    context.stopping.set()
    reconcile_execution(Claims(repository), context)
    assert context.result is not None
    assert context.result.status == "interrupted"
    assert context.result.counts.failed_assessments == 1
    assert Claims(repository).detail("CLM-1001")["run_active"] is False
    assert (
        client.post(
            "/api/claims/CLM-1001/agent-pay",
            json={"thread_id": thread, "amount": "420"},
        ).status_code
        == 409
    )
    reconcile_execution(Claims(repository), context)
    assert Claims(repository).detail("CLM-1001")["failed_attempts"] == 1


def test_queue_history_is_bounded_over_a_full_workload(client, repository, monkeypatch):
    monkeypatch.setattr(queue, "backend", lambda: api_client(client))
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    planner = ScriptedQueueModel()
    result = queue.run_queue(
        {"mode": "all"},
        lambda c, t, *, budget: main.run(c, t, scripted_model(), budget=budget),
        planner,
    )
    assert result.counts.worked == 25
    assert planner.peak_messages < 20


def test_financial_write_is_retried_only_after_confirming_no_commit(client, repository):
    thread = begin_decision_claim(repository)
    seen = []
    with api_client(client, seen) as forward:

        def unavailable_once(request):
            if request.url.path.endswith("/decision") and not seen:
                seen.append(("POST", request.url.path))
                return httpx.Response(503)
            return forward.send(request)

        with httpx.Client(
            base_url="http://testserver",
            transport=httpx.MockTransport(unavailable_once),
        ) as transport:
            response = write(
                transport,
                "/api/claims/CLM-1001",
                "/api/claims/CLM-1001/decision",
                decision_payload(thread),
            )
    assert response.status_code == 201
    assert [method for method, _ in seen] == ["POST", "GET", "POST"]


def test_tool_limit_is_an_explicit_assessment_failure(client, repository, monkeypatch):
    thread = begin_claim(repository)
    with api_client(client) as transport:
        tools = ClaimTools(transport, "CLM-1001", thread, AttemptBudget(tool_calls=1))
        tools.call("Read", "GET", tools.path)
        with pytest.raises(AgentRunError, match="tool-call limit"):
            tools.call("Read", "GET", tools.path)


@pytest.mark.parametrize("suffix", ["/reference-reads", "/finish"])
def test_lost_safe_mutation_response_replays_without_duplicate_state(
    client, repository, monkeypatch, suffix
):
    faults = []

    def lose(request, _response):
        if request.url.path.endswith(suffix) and not faults:
            faults.append(True)
            raise httpx.ReadTimeout("Synthetic", request=request)

    monkeypatch.setattr(agent_http, "sleep", lambda _delay: None)
    monkeypatch.setattr(
        queue, "backend", lambda: api_client(client, after_response=lose)
    )
    monkeypatch.setattr(
        main, "backend", lambda: api_client(client, after_response=lose)
    )
    result = queue.run_queue(
        {"mode": "claim", "claim_id": "CLM-1001"},
        lambda c, t, *, budget: main.run(c, t, scripted_model(), budget=budget),
    )
    assert result.counts.assessment_attempts == result.counts.simulated_payments == 1
    assert result.counts.failed_assessments == 0
    assert len(client.get("/api/claims/CLM-1001/evidence").json()["reads"]) == 4
