import pytest

from backend.tests.conftest import client, repository, stub_queue

__all__ = ["client", "repository", "stub_queue"]


@pytest.fixture(autouse=True)
def model_environment(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("MODEL_NAME", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
