"""The CLI loads the agent key after importing its backend client."""

import runpy
import sys
from contextlib import nullcontext

import dotenv

from agent import backend_client, checkpoints, console, harness, models
from backend import agent_key
from backend.tests.auth_support import TEST_AGENT_KEY


def test_cli_sends_the_key_loaded_after_import(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"{agent_key.ENV_NAME}={TEST_AGENT_KEY}\n", encoding="utf-8")
    monkeypatch.delenv(agent_key.ENV_NAME)
    load = dotenv.load_dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda **kw: load(env_file, **kw))
    monkeypatch.delitem(sys.modules, "agent.__main__", raising=False)
    monkeypatch.setattr(sys, "argv", ["agent", "CLM-1001"])
    monkeypatch.setattr(console, "show_reports", lambda: None)
    monkeypatch.setattr(checkpoints, "checkpointer", lambda: nullcontext(None))
    monkeypatch.setattr(models, "build_model", lambda configuration: configuration)
    monkeypatch.setattr(harness, "claim_graph", lambda model, *args, **options: model)
    configured = []
    monkeypatch.setattr(
        harness, "work_claim", lambda claim, graph: configured.append(graph)
    )
    sent = []
    call = backend_client.call

    def capture(backend, method, path, **request):
        sent.append((path, agent_key.configured_key()))
        return call(backend, method, path, **request)

    monkeypatch.setattr(backend_client, "call", capture)
    runpy.run_module("agent.__main__", run_name="__main__")
    assert sent == [
        ("/claims/CLM-1001", TEST_AGENT_KEY),
        ("/configurations/current", TEST_AGENT_KEY),
    ]
    assert configured[0]["version"] == 1
