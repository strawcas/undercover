"""AgentCore admission, initialization and task lifecycle."""

import os
from contextlib import contextmanager
from threading import Event

import pytest
from bedrock_agentcore.runtime.models import IDENTITY_WAT_HEADER
from fastapi import BackgroundTasks

from agent import api, checkpoints, dispatcher, memory, runtime_settings, service
from agent.backend_client import Refused
from agent.tests.records import latest
from agent.tests.runtime_support import invoke, runtime_client, wait_for_idle
from backend import agent_key
from backend.tests.run_requests import configuration


def test_admission_reserves_before_creating_backend_run(client):
    background = BackgroundTasks()
    assert service.start_dispatch(
        service.DispatchRequest(concurrency=25), background
    ) == {"status": "accepted"}
    try:
        assert latest(client) is None
        with pytest.raises(Refused, match="dispatch") as busy:
            service.start_dispatch(
                service.DispatchRequest(concurrency=1), BackgroundTasks()
            )
        assert busy.value.status_code == 409
    finally:
        service.run_slot.release()


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.usefixtures("runtime_defaults")
def test_worker_keeps_ping_busy_until_it_finishes(client, monkeypatch, fails):
    entered, finish = Event(), Event()

    def dispatch(*args):
        entered.set()
        assert finish.wait(timeout=10)
        if fails:
            raise RuntimeError("stand-in worker failed")

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    with runtime_client() as agent:
        assert agent.get("/ping").json()["status"] == "Healthy"
        try:
            assert invoke(agent, "start_dispatch", agents=1).json() == {
                "accepted": True
            }
            assert entered.wait(timeout=10)
            assert agent.get("/ping").json()["status"] == "HealthyBusy"
            assert invoke(agent, "health").json() == {"healthy": True}
            busy = invoke(agent, "start_dispatch", agents=3)
            assert busy.status_code == 200
            assert "dispatch" in busy.json()["refused"]
        finally:
            finish.set()
        wait_for_idle(agent)
    assert latest(client)["status"] == ("stopped" if fails else "finished")
    assert not service.run_slot.locked()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"operation": "unknown"},
        {"operation": "start_dispatch"},
        {"operation": "start_dispatch", "agents": 26},
        {"operation": "start_dispatch", "agents": "1"},
        {"operation": "resume_claim", "claim_id": ""},
    ],
)
@pytest.mark.usefixtures("runtime_defaults")
def test_invalid_invocations_return_400(body):
    reply = runtime_client().post("/invocations", json=body)
    assert reply.status_code == 400
    assert reply.json()["error"]


@pytest.mark.usefixtures("runtime_defaults")
def test_initialization_checks_key_before_opening_a_pool(monkeypatch):
    monkeypatch.delenv(agent_key.ENV_NAME)
    monkeypatch.setattr(
        checkpoints, "checkpointer", lambda: pytest.fail("opened checkpoints")
    )
    with runtime_client({}) as agent:
        assert agent.get("/ping").json()["status"] == "Healthy"
        assert invoke(agent, "health").status_code == 503


@pytest.mark.usefixtures("runtime_defaults")
def test_first_request_initialization_can_retry(monkeypatch):
    transitions = []

    @contextmanager
    def pool():
        transitions.append("open")
        try:
            yield None
        finally:
            transitions.append("close")

    def recover():
        if len(transitions) == 1:
            raise RuntimeError("initialization failed")

    monkeypatch.setattr(checkpoints, "checkpointer", pool)
    monkeypatch.setattr(service, "recover", recover)
    with runtime_client() as agent:
        assert not transitions
        assert agent.get("/ping").json()["status"] == "Healthy"
        assert not transitions
        assert invoke(agent, "health").status_code == 503
        assert transitions == ["open", "close"]
        assert invoke(agent, "health").json() == {"healthy": True}
        assert invoke(agent, "health").json() == {"healthy": True}
        assert transitions == ["open", "close", "open"]
    assert transitions == ["open", "close", "open", "close"]


@pytest.mark.usefixtures("runtime_defaults")
def test_managed_runtime_loads_three_identity_credentials_once(monkeypatch):
    calls = []
    pools = []
    for name, setting in runtime_settings.PROVIDERS.items():
        monkeypatch.setenv(setting, f"provider-{name}")
        monkeypatch.setenv(name, "original")
    monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", "https://gateway.invalid/mcp")

    def identity(*, provider_name):
        def decorate(receive):
            async def resolve():
                assert (
                    runtime_settings.BedrockAgentCoreContext.get_workload_access_token()
                    == "stand-in-wat"
                )
                calls.append(provider_name)
                return await receive(f"from-{provider_name}")

            return resolve

        return decorate

    @contextmanager
    def pool():
        pools.append({name: os.environ[name] for name in runtime_settings.CREDENTIALS})
        yield None

    monkeypatch.setattr(runtime_settings, "requires_api_key", identity)
    monkeypatch.setattr(checkpoints, "checkpointer", pool)
    monkeypatch.setattr(
        service, "resume_claim", lambda claim_id, background: {"status": "accepted"}
    )
    with runtime_client({IDENTITY_WAT_HEADER: "stand-in-wat"}) as agent:
        assert not calls
        assert not pools
        assert invoke(agent, "resume_claim", claim_id="CLM-1001").json() == {
            "accepted": True
        }
        assert invoke(agent, "health").json() == {"healthy": True}
    assert calls == [f"provider-{name}" for name in runtime_settings.CREDENTIALS]
    assert pools == [
        {name: f"from-provider-{name}" for name in runtime_settings.CREDENTIALS}
    ]


@pytest.mark.usefixtures("runtime_defaults")
def test_first_request_replays_started_memory_receipts(monkeypatch):
    retried = []
    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "stand-in-memory")
    monkeypatch.setattr(memory, "recover_pending", lambda: retried.append("once"))
    with runtime_client({}) as agent:
        assert not retried
        assert invoke(agent, "health").json() == {"healthy": True}
        wait_for_idle(agent)
        assert invoke(agent, "health").json() == {"healthy": True}
    assert retried == ["once"]


def test_interim_recovery_stops_abandoned_runs_without_changing_the_thread(monkeypatch):
    run_id = api.start_run(configuration())["run_id"]
    api.start_claim("CLM-1001", "saved-thread")
    monkeypatch.setattr(checkpoints, "clear_threads", lambda threads: None)
    service.recover()
    assert api.get_run(run_id)["status"] == "stopped"
    assert api.get_claim("CLM-1001")["thread_id"] == "saved-thread"
