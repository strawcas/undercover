"""The command line, and the model and the checkpointer it builds."""

from contextlib import nullcontext

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent import __main__ as cli
from agent import checkpoints, models
from agent.backend_client import Refused
from agent.models import build_model
from agent.tests.records import claim_of
from agent.tests.stubs import decide, stub


class FakePool:
    """Stands in for psycopg's ConnectionPool, keeping how it was opened and what
    it ran."""

    opened: list["FakePool"] = []

    def __class_getitem__(cls, _):
        return cls

    def __init__(self, conninfo, **settings):
        self.conninfo, self.settings, self.statements = conninfo, settings, []
        self.opened.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def connection(self):
        return nullcontext(self)

    def execute(self, statement):
        self.statements.append(statement)


class FakeSaver:
    """Stands in for LangGraph's PostgresSaver."""

    def __init__(self, pool):
        self.pool, self.set_up = pool, False

    def setup(self):
        self.set_up = True


def test_the_checkpointer_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pooler.example.com/db")
    monkeypatch.setattr(FakePool, "opened", [])
    monkeypatch.setattr(checkpoints, "ConnectionPool", FakePool)
    monkeypatch.setattr(checkpoints, "PostgresSaver", FakeSaver)
    with checkpoints.checkpointer() as saver:
        [pool] = FakePool.opened
        assert isinstance(saver, FakeSaver)
        assert (saver.pool, saver.set_up) == (pool, True)
        assert pool.conninfo == "postgresql://u:p@pooler.example.com/db"
        assert (pool.settings["min_size"], pool.settings["max_size"]) == (1, 4)
        kwargs = pool.settings["kwargs"]
        assert (kwargs["autocommit"], kwargs["prepare_threshold"]) == (True, 0)
        assert kwargs["row_factory"] is checkpoints.dict_row
        assert kwargs["options"] == "-c search_path=undercover"
        assert kwargs["application_name"].startswith("undercover-agent-")
        assert "undercover" in repr(pool.statements[0])


@pytest.fixture
def graph(monkeypatch):
    """Stand in for the checkpointer, the model and the claim graph the command line
    builds, and return what it builds."""
    saver = InMemorySaver()
    monkeypatch.setattr(cli, "checkpointer", lambda: nullcontext(saver))
    monkeypatch.setattr(models, "build_model", lambda configuration: "model")
    monkeypatch.setattr(cli, "claim_graph", lambda llm, s, configuration: (llm, s))
    return ("model", saver)


def test_command_line_works_one_claim(monkeypatch, capsys, graph):
    worked = []
    monkeypatch.setattr(cli, "work_claim", lambda *args: worked.append(args))
    cli.main(["CLM-1001"])
    assert worked == [("CLM-1001", graph)]

    def refuse(claim_id, claim_graph):
        raise Refused(409, f"Claim {claim_id} is not waiting")

    monkeypatch.setattr(cli, "work_claim", refuse)
    with pytest.raises(SystemExit) as refused:
        cli.main(["CLM-1003"])
    assert refused.value.code == 1
    assert capsys.readouterr().err == (
        "CLM-1003  not started: 409: Claim CLM-1003 is not waiting\n"
    )


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    default = build_model({"model": "openai/gpt-6-luna", "reasoning_effort": "low"})
    assert default.model_name == "openai/gpt-6-luna"
    assert default.reasoning_effort == "low"
    assert default.request_timeout == 120
    assert default.max_retries == 5
    free = build_model({"model": "openrouter/free", "reasoning_effort": "default"})
    assert free.model_name == "openrouter/free"
    assert free.reasoning_effort is None


def test_command_line_decides_a_claim_on_the_direct_route(client, monkeypatch):
    monkeypatch.delenv("UNDERCOVER_GATEWAY_URL", raising=False)
    monkeypatch.setattr(cli, "checkpointer", lambda: nullcontext(InMemorySaver()))
    monkeypatch.setattr(models, "build_model", lambda _: stub(*decide("approve", 420)))
    cli.main(["CLM-1001"])
    assert claim_of(client, "CLM-1001")["payment"]["amount"] == 420


def test_command_line_requires_a_claim_id(graph):
    for args in ([], ["--cap", "5"], ["CLM-1001", "--cap", "5"]):
        with pytest.raises(SystemExit):
            cli.main(args)
