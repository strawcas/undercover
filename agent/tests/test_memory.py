"""Exercise customer memory, scope and replay without AWS traffic."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from typing import cast

import pytest
from botocore.exceptions import EndpointConnectionError, NoRegionError
from botocore.stub import Stubber
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import SearchItem
from langgraph.store.memory import InMemoryStore

from agent import api, checkpoints, harness, memory, models, screening, service
from agent.tests.queue_setup import leave_waiting
from agent.tests.records import claim_of, latest
from agent.tests.stubs import calculate, call, graph_on, record, stub
from backend.tests.run_requests import configuration

CLAIM = "CLM-1002"
CUSTOMER = "CUS-3002"
SERVICE = "https://memory.invalid"


class Store(InMemoryStore):
    def __init__(self):
        super().__init__()
        self.events = {}
        self.writes = []
        self.searches = []
        self.unavailable = False
        self.interrupt = False

    def put(self, namespace, key, value, index=None, **kwargs):
        message = value["message"]
        if self.unavailable:
            raise EndpointConnectionError(endpoint_url=SERVICE)
        if self.interrupt and message.type == "ai":
            raise KeyboardInterrupt
        self.writes.append((namespace, key))
        self.events.setdefault((namespace, key), message)

    def search(self, namespace_prefix, *, query=None, **kwargs):
        self.searches.append((namespace_prefix, query))
        if self.unavailable:
            raise EndpointConnectionError(endpoint_url=SERVICE)
        timestamp = datetime.now(UTC)
        paths = {
            f"/customers/{CUSTOMER}/facts/": "Customer fact",
            f"/customers/{CUSTOMER}/claims/CLM-0450/": "Earlier claim",
            f"/customers/{CUSTOMER}/claims/{CLAIM}/": "Current summary",
            "/customers/CUS-9999/claims/CLM-0451/": "Other customer's summary",
        }
        return [
            SearchItem(
                namespace=namespace_prefix,
                key=path,
                value={"content": text, "namespaces": [path]},
                created_at=timestamp,
                updated_at=timestamp,
            )
            for path, text in paths.items()
        ]


@pytest.fixture
def store(monkeypatch):
    stand_in = Store()
    monkeypatch.setattr(
        memory, "configured", lambda *, evaluation: None if evaluation else stand_in
    )
    return stand_in


def test_saves_two_events_once_across_a_partial_save_and_sign_off(client, store):
    saver = InMemorySaver()
    model = stub(calculate(1350), record("approve", 1350))
    store.interrupt = True
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim(CLAIM, graph_on(model, saver))
    claim = claim_of(client, CLAIM)
    assert claim["payment"] is None
    assert len(store.events) == 1
    store.interrupt = False
    graph = graph_on(stub(), saver)
    assert "waiting for sign-off" in harness.carry_on(claim, graph)
    assert len(store.events) == 2
    assert store.writes[0] == store.writes[1]
    assert {actor for actor, _ in store.events} == {(CUSTOMER, CLAIM)}
    messages = {value.type: json.loads(value.text) for value in store.events.values()}
    assert messages["human"] == {
        field: claim[field]
        for field in (
            "summary",
            "claim_type",
            "incident_date",
            "incident_location",
            "items",
        )
    }
    assert messages["ai"] == {
        field: claim["decision"][field]
        for field in (
            "decision",
            "payout",
            "section",
            "reason",
        )
    }
    signed = client.post(f"/api/claims/{CLAIM}/sign-off").json()
    harness.carry_on(signed, graph)
    assert len(store.writes) == 3


def test_search_skips_this_claim_and_any_other_customer(store):
    query = "earlier claims"
    model = stub(call("search_memory", query=query), record("decline", 0))
    harness.work_claim(CLAIM, graph_on(model))
    assert store.searches == [(("customers", CUSTOMER), query)]
    assert json.loads(model.prompts[-1][-1].text) == ["Customer fact", "Earlier claim"]
    assert "search_memory" in model.tool_names


@pytest.mark.parametrize(("decision", "payout"), [("decline", 0), ("approve", 1350)])
def test_next_dispatch_recovers_memory_for_a_settled_claim(
    client, store, decision, payout
):
    leave_waiting(CLAIM)
    calculation = [calculate(payout)] if payout else []
    model = stub(*calculation, record(decision, payout))
    store.interrupt = True
    with pytest.raises(KeyboardInterrupt):
        harness.work_claim(CLAIM, graph_on(model))
    assert api.list_pending_memory() == [CLAIM]
    assert len(store.events) == 1
    if payout:
        client.post(f"/api/claims/{CLAIM}/sign-off").raise_for_status()
    assert claim_of(client, CLAIM)["status"] == "decided"
    store.interrupt = False

    service.reserve("dispatch")
    service.work_run(configuration(concurrency=1))
    assert not latest(client)["members"]
    assert not api.list_pending_memory()
    assert len(store.events) == 2
    assert store.writes[0] == store.writes[1]


def test_memory_errors_still_allow_a_decision_and_payment(client, caplog, store):
    store.unavailable = True
    model = stub(
        call("search_memory", query="earlier"), calculate(420), record("approve", 420)
    )
    harness.work_claim(CLAIM, graph_on(model))
    assert "Customer memory could not be searched" in model.prompts[1][-1].text
    assert "could not save" in caplog.text
    assert claim_of(client, CLAIM)["payment"]["amount"] == 420
    assert not store.events


def test_an_aws_setup_error_does_not_prevent_adjudication(client, monkeypatch, caplog):
    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "undercover-test-ABCDEFGHIJ")

    def unavailable(**kwargs):
        raise NoRegionError

    monkeypatch.setattr(memory, "AgentCoreMemoryStore", unavailable)
    model = stub(call("search_memory", query="previous"), record("decline", 0))
    graph = graph_on(model)
    assert "decline" in harness.work_claim(CLAIM, graph)
    assert claim_of(client, CLAIM)["status"] == "decided"
    assert api.list_pending_memory() == [CLAIM]
    assert "Customer memory could not be searched" in model.prompts[1][-1].text
    assert "could not save" in caplog.text


def test_receipt_recovery_does_not_block_claim_work_or_the_next_dispatch(
    client, monkeypatch
):
    leave_waiting(CLAIM)
    entered, resume = Event(), Event()

    def stalled():
        entered.set()
        assert resume.wait(10)

    monkeypatch.setattr(memory, "recover_pending", stalled)
    monkeypatch.setattr(
        models, "build_model", lambda configuration: stub(record("decline", 0))
    )
    monkeypatch.setattr(checkpoints, "new_saver", InMemorySaver)
    with ThreadPoolExecutor(max_workers=1) as pool:
        service.reserve("dispatch")
        run = pool.submit(service.work_run, configuration(concurrency=1))
        try:
            assert entered.wait(10)
            assert claim_of(client, CLAIM)["status"] == "decided"
            assert latest(client)["status"] == "finished"
            service.reserve("next dispatch")
            service.run_slot.release()
        finally:
            resume.set()
        run.result(timeout=10)


def test_screen_referrals_do_not_save_memory(store, monkeypatch):
    monkeypatch.setattr(
        screening,
        "check",
        lambda *args: {
            "result": {"reason": "Flagged by the screen", "instructions_found": True},
            "usage": None,
        },
    )
    harness.work_claim("CLM-1901", graph_on(stub()))
    assert not store.events


def test_evaluation_copies_do_not_bind_or_use_memory(store, monkeypatch):
    run = api.start_run(
        {
            "kind": "evaluation",
            "set_name": "evaluation",
            "tiny": True,
            "concurrency": 1,
            "configuration_version": 1,
            "round_id": None,
        }
    )
    model = stub(record("refer", 0))
    monkeypatch.setattr(models, "build_model", lambda configuration: model)
    monkeypatch.setattr(checkpoints, "new_saver", InMemorySaver)
    harness.execute_claim(run["members"][0]["claim_id"], run["run_id"], 1)
    assert "search_memory" not in model.tool_names
    assert not store.events
    assert not store.searches


def test_without_memory_configuration_no_store_or_tool():
    model = stub()
    graph = graph_on(model)
    assert graph.store is None
    assert "search_memory" not in model.tool_names


def test_provider_request_uses_stable_tokens_for_both_events(client, monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "undercover-test-ABCDEFGHIJ")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "offline-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "offline-secret-key")
    store = cast(memory.DeferredMemory, memory.configured(evaluation=False))
    claim = claim_of(client, CLAIM)
    decision = {
        "decision": "refer",
        "payout": 0,
        "section": "Claims manual 9",
        "reason": "Needs a handler",
        "decided_at": "2026-09-30T00:00:00+00:00",
    }
    requests = []

    def capture(params, **kwargs):
        requests.append(dict(params))

    client = store.sdk.client
    assert client.meta.config.connect_timeout == 3
    assert client.meta.config.read_timeout == 5
    assert client.meta.config.retries["total_max_attempts"] == 1
    client.meta.events.register_first(
        "before-parameter-build.bedrock-agentcore.CreateEvent", capture
    )
    event = {
        "memoryId": store.memory_id,
        "actorId": CUSTOMER,
        "sessionId": CLAIM,
        "eventId": "1795996800#abcdef",
        "eventTimestamp": datetime.now(UTC),
        "payload": [],
    }
    with Stubber(client) as aws:
        for _ in range(4):
            aws.add_response("create_event", {"event": event})
        memory.remember(store, claim, decision)
        memory.remember(store, claim, decision)
    tokens = [request["clientToken"] for request in requests]
    assert tokens[:2] == tokens[2:]
    assert tokens[0] != tokens[1]
    assert {request["actorId"] for request in requests} == {CUSTOMER}
    assert {request["sessionId"] for request in requests} == {CLAIM}
    assert [req["payload"][0]["conversational"]["role"] for req in requests] == [
        "USER",
        "ASSISTANT",
        "USER",
        "ASSISTANT",
    ]
