from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler

from agent import api, harness, improver, improver_api, screening
from agent.mcp_client import McpClient
from agent.tests.context_support import ContextStandIn
from backend.main import app


@pytest.fixture(autouse=True)
def local_tool_settings(monkeypatch):
    """Require tests to opt into stand-in Gateway, context and memory settings."""
    for name in (
        "UNDERCOVER_GATEWAY_URL",
        "UNDERCOVER_CONTEXT",
        "UNDERCOVER_MEMORY_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    original = McpClient.call

    def local_or_gateway(client, name, arguments):
        if client.auth is None:
            return ContextStandIn(client.url).call(name, arguments)
        return original(client, name, arguments)

    monkeypatch.setattr(McpClient, "call", local_or_gateway)
    return original


@pytest.fixture(autouse=True)
def screen(monkeypatch):
    """Clear ordinary test claims without a provider call; return the real checker."""
    original = screening.check
    monkeypatch.setattr(
        screening,
        "check",
        lambda *args: {
            "result": {"instructions_found": False, "reason": "Ordinary submission"},
            "usage": None,
        },
    )
    return original


@pytest.fixture(autouse=True)
def backend(monkeypatch):
    """Send the agent's requests to the test backend, at the same address."""
    test_backend = TestClient(app, base_url=str(api.backend.base_url))
    monkeypatch.setattr(api, "backend", test_backend)
    monkeypatch.setattr(improver_api, "backend", test_backend)


class RecordRoots(BaseCallbackHandler):
    """Stands in for Langfuse's handler, keeping the metadata of each root run."""

    def __init__(self, roots: list[dict[str, Any]]) -> None:
        self.roots = roots

    def on_chain_start(self, serialized, inputs, *, parent_run_id=None, **kwargs):
        if parent_run_id is None:
            self.roots.append(dict(kwargs.get("metadata") or {}))


@pytest.fixture(autouse=True)
def traces(monkeypatch):
    """Keep Langfuse out of the tests, and record the root runs it would trace."""
    roots: list[dict[str, Any]] = []
    monkeypatch.setattr(harness, "CallbackHandler", lambda: RecordRoots(roots))
    monkeypatch.setattr(improver, "CallbackHandler", lambda: RecordRoots(roots))
    return roots
