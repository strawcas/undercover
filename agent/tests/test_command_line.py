"""The command line, and the model and the checkpointer it builds."""

from contextlib import nullcontext

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent import __main__ as cli
from agent import api, harness


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
    monkeypatch.setattr(harness, "ConnectionPool", FakePool)
    monkeypatch.setattr(harness, "PostgresSaver", FakeSaver)
    with harness.checkpointer() as saver:
        [pool] = FakePool.opened
        assert isinstance(saver, FakeSaver)
        assert (saver.pool, saver.set_up) == (pool, True)
        assert pool.conninfo == "postgresql://u:p@pooler.example.com/db"
        assert (pool.settings["min_size"], pool.settings["max_size"]) == (1, 4)
        kwargs = pool.settings["kwargs"]
        assert (kwargs["autocommit"], kwargs["prepare_threshold"]) == (True, 0)
        assert kwargs["row_factory"] is harness.dict_row
        assert kwargs["options"] == "-c search_path=undercover"
        assert "undercover" in repr(pool.statements[0])


@pytest.fixture
def graph(monkeypatch):
    """Stand in for the checkpointer, the model and the claim graph the command line
    builds, and return what it builds."""
    saver = InMemorySaver()
    monkeypatch.setattr(cli, "checkpointer", lambda: nullcontext(saver))
    monkeypatch.setattr(cli, "model", lambda: "model")
    monkeypatch.setattr(cli, "claim_graph", lambda llm, s: (llm, s))
    return ("model", saver)


def test_command_line_works_one_claim(monkeypatch, capsys, graph):
    worked = []
    monkeypatch.setattr(cli, "work_claim", lambda *args: worked.append(args))
    cli.main(["CLM-1001"])
    assert worked == [("CLM-1001", graph)]

    def refuse(claim_id, claim_graph):
        raise api.Refused(409, f"Claim {claim_id} is not waiting")

    monkeypatch.setattr(cli, "work_claim", refuse)
    with pytest.raises(SystemExit) as refused:
        cli.main(["CLM-1003"])
    assert refused.value.code == 1
    assert capsys.readouterr().err == (
        "CLM-1003  not started: 409: Claim CLM-1003 is not waiting\n"
    )


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    default = harness.model()
    assert default.model_name == "openai/gpt-6-luna"
    assert default.reasoning_effort == "low"
    assert default.request_timeout == 120
    assert default.max_retries == 5
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert harness.model().model_name == "openrouter/free"
    assert harness.model().reasoning_effort is None


def test_command_line_requires_a_claim_id(graph):
    for args in ([], ["--cap", "5"], ["CLM-1001", "--cap", "5"]):
        with pytest.raises(SystemExit):
            cli.main(args)
