"""AgentCore admission, initialization and task lifecycle."""

import os
from contextlib import contextmanager
from json import JSONDecodeError
from threading import Event

import httpx
import pytest
from bedrock_agentcore.runtime.models import IDENTITY_WAT_HEADER

from agent import api, checkpoints, dispatcher, memory, runtime_settings, service
from agent.tests.runtime_support import invoke, runtime_client, wait_for_idle
from backend import agent_key
from backend.tests.run_requests import configuration, fixture_run


def test_agent_schedules_only_a_named_committed_run(client):
    run = (
        client.post("/api/dispatches", json={"concurrency": 25})
        .raise_for_status()
        .json()
    )
    assert api.get_run(run["run_id"])["concurrency"] == 25
    assert client.post("/api/dispatches", json={"concurrency": 1}).status_code == 409


@pytest.mark.usefixtures("runtime_defaults")
def test_entrypoint_reports_definite_take_refusal_before_accepting(client):
    run = fixture_run(configuration())
    api.stop_run(run["run_id"])
    agent = runtime_client()
    response = invoke(agent, "work_run", run_id=run["run_id"])
    assert response.json() == {"refused": "This run is no longer available"}
    wait_for_idle(agent)


@pytest.mark.usefixtures("runtime_defaults")
@pytest.mark.parametrize(
    "problem", [httpx.ReadTimeout("no reply"), JSONDecodeError("invalid reply", "", 0)]
)
def test_entrypoint_preserves_uncertainty_when_take_loses_its_reply(
    monkeypatch, problem
):
    def lost_reply(run_id):
        raise problem

    monkeypatch.setattr(api, "take_run", lost_reply)
    agent = runtime_client()
    assert invoke(agent, "work_run", run_id="new-run").json() == {"uncertain": True}
    wait_for_idle(agent)


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
    run_id = fixture_run(configuration(concurrency=1))["run_id"]
    untouched = fixture_run(configuration(concurrency=2))["run_id"]
    with runtime_client() as agent:
        assert agent.get("/ping").json()["status"] == "Healthy"
        try:
            assert invoke(agent, "work_run", run_id=run_id).json() == {"accepted": True}
            assert entered.wait(timeout=10)
            assert agent.get("/ping").json()["status"] == "HealthyBusy"
            assert invoke(agent, "health").json() == {"healthy": True}
        finally:
            finish.set()
        wait_for_idle(agent)
    assert api.get_run(run_id)["status"] == ("stopped" if fails else "finished")
    assert api.get_run(untouched)["status"] == "running"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"operation": "unknown"},
        {"operation": "work_run"},
        {"operation": "work_run", "run_id": 26},
        {"operation": "work_round", "round_id": ""},
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
            if len(transitions) == 1:
                raise RuntimeError("initialization failed")
            yield None
        finally:
            transitions.append("close")

    monkeypatch.setattr(checkpoints, "checkpointer", pool)
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


@pytest.mark.usefixtures("runtime_defaults")
def test_new_runtime_session_leaves_existing_work_to_its_owner():
    run_id = fixture_run(configuration())["run_id"]
    api.start_claim("CLM-1001", "saved-thread")
    with runtime_client({}) as agent:
        assert invoke(agent, "health").json() == {"healthy": True}
    assert api.get_run(run_id)["status"] == "running"
    assert api.get_claim("CLM-1001")["thread_id"] == "saved-thread"
