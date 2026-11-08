"""Immediate admission, background lifecycle and startup recovery."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient

from agent import api, dispatcher, harness, service
from backend.tests.run_requests import configuration


def test_admission_reserves_before_creating_backend_run(client, monkeypatch):
    background = BackgroundTasks()
    assert service.start_dispatch(
        service.DispatchRequest(concurrency=25), background
    ) == {"status": "accepted"}
    try:
        assert api.get_latest_run() is None
        with pytest.raises(HTTPException) as busy:
            service.start_dispatch(
                service.DispatchRequest(concurrency=1), BackgroundTasks()
            )
        assert busy.value.status_code == 409
    finally:
        service.run_slot.release()


def test_background_failure_releases_admission(monkeypatch, caplog):
    def fail(*args):
        raise RuntimeError("cannot create run")

    monkeypatch.setattr(api, "start_run", fail)
    monkeypatch.setattr(harness, "clear_threads", lambda threads: None)
    assert service.run_slot.acquire(blocking=False)
    service.work_run(configuration())
    assert not service.run_slot.locked()
    assert "cannot create run" in caplog.text


def test_background_dispatch_does_not_block_health(monkeypatch):
    entered, finish = Event(), Event()

    def dispatch(*args):
        entered.set()
        assert finish.wait(timeout=10)

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    monkeypatch.setenv("MODEL_NAME", "stub")
    monkeypatch.setattr(service, "checkpointer", lambda: nullcontext(None))
    monkeypatch.setattr(harness, "clear_threads", lambda threads: None)
    with TestClient(service.app) as agent, ThreadPoolExecutor(max_workers=1) as workers:
        response = workers.submit(
            agent.post, "/api/dispatches", json={"concurrency": 1}
        )
        try:
            assert entered.wait(timeout=10)
            assert agent.get("/api/health").json() == {"status": "ok"}
            assert (
                agent.post("/api/dispatches", json={"concurrency": 3}).status_code
                == 409
            )
        finally:
            finish.set()
        assert response.result().status_code == 202
    run = api.get_latest_run()
    assert run is not None
    assert run["status"] == "finished"
    assert not service.run_slot.locked()


@pytest.mark.parametrize("n", [0, 26])
def test_service_concurrency_limits(n):
    assert (
        TestClient(service.app)
        .post("/api/dispatches", json={"concurrency": n})
        .status_code
        == 422
    )


def test_startup_waits_then_stops_abandoned_runs(monkeypatch):
    run_id = api.start_run(configuration())["run_id"]
    api.start_claim("CLM-1001", "saved-thread")
    calls = []

    def health():
        calls.append("health")
        if len(calls) == 1:
            raise httpx.ConnectError("backend still starting")
        return {"status": "ok"}

    async def wait(delay):
        calls.append("wait")

    cleared = []
    monkeypatch.setattr(api, "health", health)
    monkeypatch.setattr(service.asyncio, "sleep", wait)
    monkeypatch.setattr(service, "checkpointer", lambda: nullcontext(None))
    monkeypatch.setattr(harness, "clear_threads", cleared.extend)
    with TestClient(service.app) as agent:
        assert agent.get("/api/health").status_code == 200
    assert calls == ["health", "wait", "health"]
    assert api.get_run(run_id)["status"] == "stopped"
    assert api.get_claim("CLM-1001")["thread_id"] == "saved-thread"
