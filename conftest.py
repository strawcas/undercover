"""Fixtures for the backend's and the agent's tests."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import agent_key, agent_service, auth, judge
from backend.database import LAMBDA_MARKER, engine, seed
from backend.judge_models import Grade
from backend.main import app
from backend.tests.auth_support import (
    AGENT_HEADERS,
    PUBLIC_JWK,
    TEST_AGENT_KEY,
    authorization,
)


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    """Give every test its own freshly seeded SQLite database, never Postgres."""
    path = tmp_path / "undercover.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(LAMBDA_MARKER, raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("UNDERCOVER_AGENT_RUNTIME", raising=False)
    monkeypatch.delenv(agent_service.RUNTIME_USER_ID_ENV, raising=False)
    monkeypatch.setenv(agent_key.ENV_NAME, TEST_AGENT_KEY)
    monkeypatch.setenv("UNDERCOVER_DB", str(path))
    seed(engine())
    return path


@pytest.fixture
def agent_answers(monkeypatch):
    """Answer the backend's calls to the agent's service with a handler of the test's
    own, in place of the service."""

    def answer_with(handler):
        transport = httpx.MockTransport(handler)
        stand_in = httpx.Client(
            base_url=agent_service.agent.base_url, transport=transport
        )
        monkeypatch.setattr(agent_service, "agent", stand_in)

    return answer_with


@pytest.fixture(autouse=True)
def agent_calls(agent_answers):
    """Stand in for AgentCore and keep the payload of each invocation."""
    calls: list[dict] = []

    def accept(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"accepted": True, "round_id": "stand-in"})

    agent_answers(accept)
    return calls


@pytest.fixture
def client():
    return TestClient(app, headers=authorization() | AGENT_HEADERS)


@pytest.fixture(autouse=True)
def auth_keys(monkeypatch):
    """Verify test signatures locally instead of fetching real project keys."""
    monkeypatch.setattr(auth, "SUPABASE_URL", "http://test-auth")
    monkeypatch.setattr(auth.keys, "fetch_data", lambda: {"keys": [PUBLIC_JWK]})


@pytest.fixture(autouse=True)
def stub_judge(monkeypatch):
    """Keep every automated evaluation on a local judge stand-in."""
    monkeypatch.setattr(
        judge,
        "grade",
        lambda state: Grade(
            grade=1, letter="C", same_grounds=0.6, same_section=0.7, judge_cost=0.0001
        ),
    )
