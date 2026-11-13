from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler

from agent import api, harness, improver, improver_api
from backend.main import app


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
