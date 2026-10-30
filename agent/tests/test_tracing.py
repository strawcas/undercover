from contextlib import contextmanager

from agent import tracing
from agent.tests.stubs import api_client
from agent.tools import ClaimTools
from backend.payouts import PayoutLine
from backend.tests.conftest import begin_claim


def test_no_keys_means_no_tracing(monkeypatch):
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with tracing.claim_trace("C", "T") as callbacks:
        assert callbacks == []


def test_session_and_export_failure_are_observational(monkeypatch, caplog):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "stub")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "stub")
    sessions = []
    marker = object()

    @contextmanager
    def scope(**attributes):
        sessions.append(attributes["session_id"])
        yield

    class Client:
        def flush(self):
            raise ValueError("PRIVATE-TRACE")

    monkeypatch.setattr(tracing, "CallbackHandler", lambda: marker)
    monkeypatch.setattr(tracing, "propagate_attributes", scope)
    monkeypatch.setattr(tracing, "get_client", Client)
    for thread in ["T", "T", "U"]:
        with tracing.claim_trace("C", thread) as callbacks:
            assert callbacks == [marker]
    assert sessions == ["C:T", "C:T", "C:U"]
    assert "Export claim traces" in caplog.text
    assert "PRIVATE-TRACE" not in caplog.text


def test_setup_failure_does_not_abort_work(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "stub")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "stub")

    def fail():
        raise RuntimeError("unavailable")

    monkeypatch.setattr(tracing, "CallbackHandler", fail)
    with tracing.claim_trace("C", "T") as callbacks:
        assert callbacks == []


def test_payout_tool_uses_backend_http(client, repository):
    thread = begin_claim(repository)
    seen = []
    with api_client(client, seen) as transport:
        tools = ClaimTools(transport, "CLM-1001", thread)
        result = tools.calculate_payout(
            "trip_delay",
            [
                PayoutLine.model_validate(
                    dict(
                        description="Covered taxi",
                        amount="12.34",
                        quantity=1,
                        kind="expense",
                        nights=0,
                        hours="0",
                    )
                )
            ],
            "0",
        )
    assert result["payout"] == "12.34"
    assert ("POST", "/api/claims/CLM-1001/payout-calculation") in seen
