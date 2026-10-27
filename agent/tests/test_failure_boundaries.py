import json
from contextlib import contextmanager
from time import monotonic

import httpx
import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from agent import main, queue
from agent.execution import AgentRunError, AttemptBudget, Execution, QueueStopped
from agent.invocation import invoke_helper
from agent.middleware import ProviderRetry
from agent.queue_tools import QueueTools
from agent.tests.stubs import api_client, scripted_model
from agent.tools import ClaimTools
from backend.claims import Claims
from backend.harness_models import Attempt
from backend.runs import Runs
from backend.tests.conftest import begin_claim


@pytest.mark.parametrize("lost_after_commit", [False, True])
def test_activity_failure_after_approval_does_not_prevent_payment(
    client, repository, monkeypatch, caplog, lost_after_commit
):
    faults = []

    @contextmanager
    def transport():
        with api_client(client) as forward:

            def send(request):
                if request.url.path.endswith("/events"):
                    body = json.loads(request.content)
                    if (
                        body["action"] == "Save decision"
                        and body["status"] == "completed"
                    ):
                        faults.append(True)
                        if lost_after_commit:
                            forward.send(request)
                            raise httpx.ReadTimeout(
                                "PRIVATE-EVENT-BODY", request=request
                            )
                        return httpx.Response(
                            503, json={"detail": "PRIVATE-EVENT-BODY"}
                        )
                return forward.send(request)

            with httpx.Client(
                base_url="http://testserver", transport=httpx.MockTransport(send)
            ) as connection:
                yield connection

    monkeypatch.setattr(main, "backend", transport)
    thread = begin_claim(repository)
    main.run("CLM-1001", thread, scripted_model())
    Claims(repository).finish("CLM-1001", thread)
    claim = Claims(repository).detail("CLM-1001")
    assert claim["payment"]["amount"] == "420.00"
    assert claim["review_reason"] is None
    assert len(faults) == 1
    assert "Save claim activity" in caplog.text
    assert "PRIVATE-EVENT-BODY" not in caplog.text


def test_model_retry_continues_when_retry_activity_cannot_be_saved(monkeypatch, caplog):
    monkeypatch.setattr(
        "langchain.agents.middleware.model_retry.time.sleep", lambda _: None
    )
    with httpx.Client(
        base_url="http://testserver",
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    ) as connection:
        tools = ClaimTools(connection, "TEST", "thread")
        calls = []

        def recover(_request):
            calls.append(True)
            if len(calls) == 1:
                raise httpx.ConnectError("PRIVATE-PROVIDER-BODY")
            return ModelResponse(result=[AIMessage(content="Recovered")])

        request = ModelRequest(
            model=scripted_model(), messages=[], state={"messages": []}, runtime=None
        )
        result = ProviderRetry(tools.model_retry).wrap_model_call(request, recover)
    assert isinstance(result, ModelResponse)
    assert len(calls) == 2
    assert "Save claim activity" in caplog.text
    assert "PRIVATE-PROVIDER-BODY" not in caplog.text


def test_helper_and_attempt_cleanup_failure_preserve_the_original_cause(
    client, repository, monkeypatch, caplog
):
    primary = TypeError("PRIVATE-CUSTOMER-DETAIL")

    def helper(_claim, _thread, *, budget):
        raise primary

    def fail_finish(*_args):
        raise httpx.ConnectError("PRIVATE-CLEANUP-BODY")

    monkeypatch.setattr(queue, "backend", lambda: api_client(client))
    monkeypatch.setattr(QueueTools, "finish_attempt", fail_finish)
    context = Execution()
    with pytest.raises(AgentRunError, match="TypeError.*Diagnostic") as error:
        queue.run_queue({"mode": "limited", "max_claims": 1}, helper, execution=context)
    assert error.value.__cause__ is primary
    assert "Attempt cleanup also failed" in str(primary.__notes__)
    assert "test_failure_boundaries.py" in caplog.text
    assert ":helper" in caplog.text
    assert "TypeError" in caplog.text
    assert "PRIVATE-" not in caplog.text
    assert context.result is not None
    assert context.result.status == "failed"
    assert Claims(repository).detail("CLM-1001")["run_active"] is False
    assert "PRIVATE-" not in str(Claims(repository).events("CLM-1001"))


@pytest.mark.parametrize("interrupt", [False, True])
def test_queue_cleanup_failure_cannot_replace_the_primary_error(
    client, monkeypatch, interrupt, caplog
):
    primary = (
        KeyboardInterrupt() if interrupt else AgentRunError("Original queue failure")
    )

    def fail_work(*_args):
        raise primary

    def fail_finish(*_args):
        raise httpx.ConnectError("PRIVATE-CLEANUP-BODY")

    monkeypatch.setattr(queue, "backend", lambda: api_client(client))
    monkeypatch.setattr(queue, "work_queue", fail_work)
    monkeypatch.setattr(QueueTools, "finish_run", fail_finish)
    with pytest.raises(type(primary)) as error:
        queue.run_queue({"mode": "limited", "max_claims": 1})
    assert error.value is primary
    assert "Finalize queue run also failed" in str(primary.__notes__)
    assert "PRIVATE-" not in caplog.text


@pytest.mark.parametrize("failure_at", ["claim_read", "observer"])
def test_saved_attempt_reporting_failure_does_not_stop_the_queue(
    client, repository, monkeypatch, caplog, failure_at
):
    def lose_report(request, _response):
        if (
            request.method == "GET"
            and request.url.path == "/api/claims/CLM-1001"
            and not Claims(repository).detail("CLM-1001")["run_active"]
        ):
            raise httpx.ReadTimeout("PRIVATE-REPORT-BODY", request=request)

    def observer(_result):
        if failure_at == "observer":
            raise ValueError("PRIVATE-OBSERVER-BODY")

    monkeypatch.setattr("agent.http.sleep", lambda _: None)
    monkeypatch.setattr(
        queue,
        "backend",
        lambda: api_client(
            client, after_response=lose_report if failure_at == "claim_read" else None
        ),
    )
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    result = queue.run_queue(
        {"mode": "limited", "max_claims": 2},
        lambda c, t, *, budget: main.run(c, t, scripted_model(), budget=budget),
        execution=Execution(on_result=observer),
    )
    assert result.counts.simulated_payments == 2
    assert result.counts.failed_assessments == 0
    assert "Report saved assessment" in caplog.text
    assert "PRIVATE-" not in caplog.text


def test_helper_receives_the_reserved_deadline_and_cancellation_signal(repository):
    begin_claim(repository)
    current = Runs(repository).current()
    assert current is not None
    entry = Attempt.model_validate(current["attempts"][0])
    context = Execution()

    def helper(claim_id, thread_id, *, budget):
        assert claim_id == entry.claim_id
        assert thread_id == entry.thread_id
        assert budget.cancelled is context.cancel_active
        assert 0 < budget.deadline - monotonic() <= 600
        return "Complete"

    assert invoke_helper(helper, entry, context) == "Complete"


@pytest.mark.parametrize("cancelled", [False, True])
def test_budget_is_checked_before_claim_http_calls(monkeypatch, cancelled):
    seen = []
    budget = AttemptBudget(deadline=monotonic() - 1)
    if cancelled:
        budget.cancelled.set()

    def transport():
        def unexpected_request(request):
            seen.append(request)
            return httpx.Response(500)

        return httpx.Client(
            base_url="http://testserver",
            transport=httpx.MockTransport(unexpected_request),
        )

    monkeypatch.setattr(main, "backend", transport)
    with pytest.raises(QueueStopped if cancelled else AgentRunError):
        main.run("TEST", "thread", scripted_model(), budget=budget)
    assert seen == []
