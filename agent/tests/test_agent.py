import inspect

import httpx
import pytest

from agent import main
from agent.tests.stubs import api_client, scripted_model
from agent.tools import ClaimTools
from backend.claims import Claims


@pytest.mark.parametrize(
    ("outcome", "payout"),
    [("approve", "420.00"), ("approve", "1250.00"), ("decline", "0"), ("refer", "0")],
)
def test_scripted_run(client, repository, monkeypatch, outcome, payout):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    claims = Claims(repository)
    thread = claims.start("CLM-1001")
    reply = main.run("CLM-1001", thread, scripted_model(outcome, payout))
    claims.finish("CLM-1001", thread)
    assert "complete" in reply
    detail = claims.detail("CLM-1001")
    assert detail["decision"]["outcome"] == outcome
    assert detail["decision"]["policy_sections"] == ["3", "10", "11"]
    events = claims.events("CLM-1001")
    assert all(event["thread_id"] == thread for event in events)
    actions = {event["action"] for event in events}
    assert {
        "Read claim and customer history",
        "Read policy wording",
        "Read payout approval limit",
        "Read agent instructions",
    } <= actions
    if payout == "1250.00":
        assert detail["status"] == "awaiting_sign_off"
        assert detail["payment"] is None
        assert any(event["status"] == "blocked" for event in events)
    elif outcome == "approve":
        assert detail["payment"]["amount"] == "420.00"
    else:
        assert detail["status"] == "decided"
        assert detail["payment"] is None


def test_model_configuration(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    default = main.model()
    assert default.model_name == "openai/gpt-6-luna"
    assert default.reasoning_effort == "low"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert main.model().model_name == "openrouter/free"
    assert main.model().reasoning_effort is None


def test_backend_url(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_BACKEND_URL", "http://localhost:8765")
    with main.backend() as client:
        assert str(client.base_url) == "http://localhost:8765"


def test_backend_instructions_are_used(client, repository, monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    original = main.create_agent
    seen = []

    def create(*args, **kwargs):
        seen.append(kwargs["system_prompt"])
        return original(*args, **kwargs)

    monkeypatch.setattr(main, "create_agent", create)
    thread = Claims(repository).start("CLM-1001")
    main.run("CLM-1001", thread, scripted_model())
    assert seen == [client.get("/api/agent-instructions").json()["text"]]


def test_tools_use_only_http(client, repository):
    thread = Claims(repository).start("CLM-1001")
    with api_client(client) as transport:
        tools = ClaimTools(transport, "CLM-1001", thread).tools()
        assert {tool.name for tool in tools} == {
            "read_claim",
            "read_policy_wording",
            "read_company",
            "record_decision",
            "pay_claim",
        }
    source = inspect.getsource(main) + inspect.getsource(ClaimTools)
    assert "backend.storage" not in source
    assert "read_text" not in source
    assert "data/" not in source


def test_tool_http_error(client, repository):
    thread = Claims(repository).start("CLM-1001")
    with api_client(client) as transport:
        tools = ClaimTools(transport, "CLM-1001", thread)
        assert tools.call("Missing operation", "GET", "/api/missing")["status"] == 404
    assert Claims(repository).events("CLM-1001")[-1]["status"] == "failed"


def test_instruction_http_error(monkeypatch):
    def unavailable():
        return httpx.Client(
            base_url="http://testserver",
            transport=httpx.MockTransport(lambda _r: httpx.Response(503)),
        )

    monkeypatch.setattr(main, "backend", unavailable)
    with pytest.raises(httpx.HTTPStatusError):
        main.run("CLM-1001", "thread", scripted_model())
