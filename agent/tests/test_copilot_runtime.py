from threading import Event
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from fastapi.testclient import TestClient

from agent.copilot import entrypoint, memory
from agent.memory import DeferredMemory


def test_runtime_health_and_busy_workers(monkeypatch):
    ready, release = Event(), Event()
    calls = []

    async def initialize(managed):
        calls.append(managed)

    def work(*_args):
        ready.set()
        release.wait(5)

    monkeypatch.setattr(entrypoint.settings, "initialize", initialize)
    monkeypatch.setattr(entrypoint.worker, "run", work)
    assert entrypoint.settings.required == ("OPENROUTER_API_KEY", "DATABASE_URL")
    with TestClient(entrypoint.app) as client:
        assert not calls
        assert client.post("/invocations", json={"operation": "health"}).json() == {
            "healthy": True
        }
        request = {"operation": "start", "bearer": "stand-in"} | {
            field: str(uuid4())
            for field in ("conversation_id", "turn_id", "stretch_id")
        }
        try:
            assert client.post("/invocations", json=request).json() == {
                "accepted": True
            }
            assert ready.wait(2)
            assert client.get("/ping").json()["status"] == "HealthyBusy"
        finally:
            release.set()


def test_handler_memory_ignores_mismatched_namespaces():
    class Store:
        def search(self, namespace, **_query):
            actor, kind = namespace[1:]
            return [
                SimpleNamespace(
                    value={
                        "content": kind,
                        "namespaces": [f"/handlers/{actor}/{kind}/"],
                    }
                ),
                SimpleNamespace(
                    value={
                        "content": "private",
                        "namespaces": [f"/handlers/HND-02/{kind}/"],
                    }
                ),
            ]

    assert memory.search(
        cast(DeferredMemory, Store()), "HND-01", "How do I like answers?"
    ) == [
        "preferences",
        "facts",
    ]
