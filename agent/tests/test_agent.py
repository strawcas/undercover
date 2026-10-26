import inspect

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from ollama import ResponseError
from pydantic import SecretStr

from agent import main
from agent.tests.stubs import StubModel, api_client, scripted_model
from agent.tools import ClaimTools
from backend.claims import Claims
from backend.tests.conftest import begin_claim
from backend.worker import execute


@pytest.mark.parametrize(
    ("outcome", "payout"),
    [("approve", "420.00"), ("approve", "1250.00"), ("decline", "0"), ("refer", "0")],
)
def test_scripted_run(client, repository, monkeypatch, outcome, payout):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    claims = Claims(repository)
    thread = begin_claim(repository)
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
        "Read reference section",
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
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    default = main.model()
    assert isinstance(default, ChatOpenAI)
    assert default.model_name == "deepseek/deepseek-v4.1-flash"
    assert default.reasoning_effort == "low"
    assert str(default.openai_api_base) == main.OPENROUTER_BASE_URL
    assert isinstance(default.openai_api_key, SecretStr)
    assert default.openai_api_key.get_secret_value() == "test-key"
    monkeypatch.setenv("MODEL_NAME", main.DEFAULT_MODEL)
    explicit_default = main.model()
    assert isinstance(explicit_default, ChatOpenAI)
    assert explicit_default.reasoning_effort == "low"
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    selected = main.model()
    assert isinstance(selected, ChatOpenAI)
    assert selected.model_name == "openrouter/free"
    assert selected.reasoning_effort is None


@pytest.mark.parametrize("name", ["qwen3:8b", "llama3.1:8b"])
def test_ollama_model_configuration(monkeypatch, name):
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("MODEL_NAME", name)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    selected = main.model()
    assert isinstance(selected, ChatOllama)
    assert selected.model == name
    assert selected.base_url == "http://localhost:11434"
    assert selected.temperature == 0
    assert selected.num_ctx == 16384
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:12345")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "32768")
    custom = main.model()
    assert isinstance(custom, ChatOllama)
    assert custom.base_url == "http://localhost:12345"
    assert custom.num_ctx == 32768


@pytest.mark.parametrize("length", ["", "0", "-1", "1.5", "many"])
def test_ollama_rejects_invalid_context_length(monkeypatch, length):
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("MODEL_NAME", "gemma4:e4b")
    monkeypatch.setenv("OLLAMA_NUM_CTX", length)
    with pytest.raises(ValueError, match="OLLAMA_NUM_CTX must be a positive integer"):
        main.validate_model_config()


def test_openrouter_ignores_ollama_context_setting(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "unused")
    assert isinstance(main.model(), ChatOpenAI)


@pytest.mark.parametrize("saved", [False, True])
def test_truncated_response_has_actionable_failure(
    client, repository, monkeypatch, saved
):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    replies = list(scripted_model().messages)[:-1] if saved else []
    replies.append(AIMessage(content="", response_metadata={"done_reason": "length"}))
    llm = StubModel(messages=iter(replies))
    claims = Claims(repository)
    thread = begin_claim(repository)
    execute(claims, "CLM-1001", thread, lambda c, t: main.run(c, t, llm))
    detail = claims.detail("CLM-1001")
    assert detail["status"] == ("decided" if saved else "waiting")
    assert detail["run_active"] is False
    assert bool(detail["decision"]) == saved
    assert bool(detail["payment"]) == saved
    event = claims.events("CLM-1001")[-1]
    assert event["status"] == "failed"
    assert "context or generation limit" in event["detail"]
    assert "OLLAMA_NUM_CTX" in event["detail"]


@pytest.mark.parametrize(
    ("status", "message"),
    [(404, "ollama list"), (400, "tool calling"), (500, "HTTP 500")],
)
def test_ollama_failure_is_actionable_and_redacted(
    client, repository, monkeypatch, status, message
):
    monkeypatch.setattr(main, "backend", lambda: api_client(client))

    def failed_generation(*args, **kwargs):
        raise ResponseError("secret provider details", status_code=status)

    monkeypatch.setattr(StubModel, "_generate", failed_generation)
    claims = Claims(repository)
    thread = begin_claim(repository)
    execute(claims, "CLM-1001", thread, lambda c, t: main.run(c, t, scripted_model()))
    detail = claims.detail("CLM-1001")
    assert detail["status"] == "waiting"
    assert detail["decision"] is None
    assert detail["run_active"] is False
    events = claims.events("CLM-1001")
    assert events[-1]["status"] == "failed"
    assert message in events[-1]["detail"]
    assert "secret" not in str(events)


@pytest.mark.parametrize("provider", ["unknown", ""])
def test_unknown_model_provider(monkeypatch, provider):
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    with pytest.raises(ValueError, match="MODEL_PROVIDER must be openrouter or ollama"):
        main.model()


@pytest.mark.parametrize("name", [None, "", "   "])
def test_ollama_requires_a_model_name(monkeypatch, name):
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    if name is None:
        monkeypatch.delenv("MODEL_NAME", raising=False)
    else:
        monkeypatch.setenv("MODEL_NAME", name)
    with pytest.raises(ValueError, match="Set MODEL_NAME"):
        main.model()


def test_openrouter_requires_a_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Set OPENROUTER_API_KEY"):
        main.model()


@pytest.mark.parametrize(
    ("provider", "factory"), [("openrouter", "ChatOpenAI"), ("ollama", "ChatOllama")]
)
def test_selected_provider_runs_with_existing_http_tools(
    client, repository, monkeypatch, provider, factory
):
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    monkeypatch.setenv("MODEL_NAME", "test-model")
    if provider == "ollama":
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    seen = []

    def configured_model(**kwargs):
        seen.append(kwargs)
        return scripted_model()

    monkeypatch.setattr(main, factory, configured_model)
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    claims = Claims(repository)
    thread = begin_claim(repository)
    assert "complete" in main.run("CLM-1001", thread)
    claims.finish("CLM-1001", thread)
    assert seen[0]["model"] == "test-model"
    assert claims.detail("CLM-1001")["payment"]["amount"] == "420.00"


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
    thread = begin_claim(repository)
    main.run("CLM-1001", thread, scripted_model())
    assert seen == [client.get("/api/agent-instructions").json()["text"]]


def test_tools_use_only_http(client, repository):
    thread = begin_claim(repository)
    with api_client(client) as transport:
        tools = ClaimTools(transport, "CLM-1001", thread).tools()
        assert {tool.name for tool in tools} == {
            "read_claim",
            "list_reference_sections",
            "search_references",
            "read_reference_section",
            "read_company",
            "record_decision",
            "pay_claim",
        }
    source = inspect.getsource(main) + inspect.getsource(ClaimTools)
    assert "backend.storage" not in source
    assert "read_text" not in source
    assert "data/" not in source


def test_tool_http_error(client, repository):
    thread = begin_claim(repository)
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


@pytest.mark.parametrize(
    "scenario",
    [
        ("repeat-claim review", "9", "refer", "0"),
        ("wild-animal interpretation", "7.wild-animals", "approve", "100.00"),
        ("meals without receipts", "10", "approve", "70.00"),
    ],
)
def test_manual_case_workflows_are_audited(client, repository, monkeypatch, scenario):
    case, manual, outcome, payout = scenario
    fixture = repository.record("claim", "CLM-1001") | {
        "claim_id": "TEST-MANUAL",
        "summary": case,
        "story": f"Synthetic workflow fixture for {case}; outcome supplied by a stub.",
    }
    repository.add_claim(fixture)
    monkeypatch.setattr(main, "backend", lambda: api_client(client))
    thread = begin_claim(repository, "TEST-MANUAL")
    main.run(
        "TEST-MANUAL",
        thread,
        scripted_model(
            outcome,
            payout,
            manual_sections=(manual, "6"),
        ),
    )
    Claims(repository).finish("TEST-MANUAL", thread)
    record = client.get("/api/claims/TEST-MANUAL").json()
    assert record["decision"]["outcome"] == outcome
    assert record["decision"]["manual_sections"] == [manual, "6"]
    evidence = client.get("/api/claims/TEST-MANUAL/evidence").json()["reads"]
    assert any(
        item["section_id"] == manual and item["source"] == "claims_manual"
        for item in evidence
    )
