"""Exercise the signed MCP route with a local stand-in for the Gateway."""

import asyncio
import json
from typing import cast

import httpx
import pytest
from botocore.credentials import Credentials
from langchain.tools import ToolException
from langchain_core.runnables import RunnableLambda
from mangum.types import LambdaContext
from sqlalchemy import update

from agent import api, gateway, harness, mcp_client, screening
from agent.tests.context_support import ContextStandIn
from agent.tests.records import claim_of, steps_of
from agent.tests.stubs import (
    calculate,
    calculate_for_claim,
    call,
    graph_on,
    record,
    stub,
)
from backend.database import engine, settings
from backend.main import handler
from backend.tests.claim_requests import sign_off

URL = "https://gateway.example.invalid/mcp"
DENIAL = "Tool Execution Denied: Policy refused payment"
REGION = "us-east-1"
SESSION_VALUE = "test-session-value"


class Session:
    region_name = REGION

    def get_credentials(self):
        return Credentials("test-key", "test-secret", SESSION_VALUE)


def response(value):
    return {"content": [{"type": "text", "text": json.dumps(value)}]}


class GatewayStandIn:
    def __init__(self, app):
        self.app = app
        self.calls = []

    def tool(self, name, arguments):
        self.calls.append((name, arguments.copy()))
        if name.startswith("undercover-context___"):
            operation = name.removeprefix("undercover-context___")
            try:
                return {
                    "result": response(ContextStandIn(URL).call(operation, arguments))
                }
            except ToolException as error:
                return {
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": str(error)}],
                    }
                }
        operation = name.removeprefix("undercover-app___")
        if operation == "get_policy" and arguments["policy_id"] == "POL-2002":
            return {"error": {"code": -32000, "message": DENIAL}}
        if operation == "pay_claim" and arguments["amount"] > 1000:
            return {"error": {"code": -32000, "message": DENIAL}}
        claim_id = arguments.pop("claim_id", "")
        method, path = {
            "get_claim": ("GET", f"/api/claims/{claim_id}"),
            "get_policy": ("GET", f"/api/policies/{arguments.get('policy_id')}"),
            "get_claim_history": (
                "GET",
                f"/api/customers/{arguments.get('customer_id')}/claims",
            ),
            "calculate_payout": ("POST", f"/api/claims/{claim_id}/payout"),
            "record_decision": ("POST", f"/api/claims/{claim_id}/decision"),
            "pay_claim": ("POST", f"/api/claims/{claim_id}/payment"),
        }[operation]
        app = self.app.request(
            method,
            path,
            **({"params": arguments} if method == "GET" else {"json": arguments}),
        )
        if app.is_error:
            result = {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": "Client error: API request failed with status: "
                        f"{app.status_code} - {app.text}",
                    }
                ],
            }
        else:
            result = response(app.json())
        return {"result": result}

    def receive(self, request):
        assert request.url == httpx.URL(URL)
        assert (
            f"/{REGION}/bedrock-agentcore/aws4_request"
            in request.headers["authorization"]
        )
        assert request.headers["x-amz-security-token"] == SESSION_VALUE
        assert request.headers["x-amz-date"]
        message = json.loads(request.content)
        if message["method"] == "notifications/initialized":
            return httpx.Response(202)
        params = message.get("params", {})
        if message["method"] == "initialize":
            body = {
                "result": {
                    "protocolVersion": params["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test Gateway", "version": "1"},
                }
            }
        elif message["method"] == "tools/list":
            body = {"result": {"tools": []}}
        else:
            assert message["method"] == "tools/call"
            body = self.tool(params["name"], params["arguments"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], **body})


@pytest.fixture
def stand_in(client, monkeypatch):
    stand_in = GatewayStandIn(client)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(gateway.boto3, "Session", Session)
    monkeypatch.setattr(
        mcp_client.httpx,
        "AsyncClient",
        lambda **options: real_client(
            transport=httpx.MockTransport(stand_in.receive), **options
        ),
    )
    monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", URL)
    return stand_in


def test_signed_route_injects_ids_and_pays(client, stand_in):
    claim_id = "CLM-1001"
    with_gateway = gateway.configured()
    direct = api.claim_text(claim_id)
    assert api.claim_text(claim_id, via=with_gateway) == direct
    stand_in.calls.clear()
    model = stub(
        call("get_claim"),
        call("get_policy"),
        call("get_claim_history"),
        call("search_documents", query="trip delay"),
        call("read_section", document="policy_wording", number=3),
        record("approve", 0),
        calculate(420),
        record("approve", 420),
    )
    harness.work_claim(claim_id, graph_on(model))
    assert claim_of(client, claim_id)["payment"]["amount"] == 420
    assert stand_in.calls[-2][1]["decided_by"] == "UnderCover Agent (configuration 1)"
    assert stand_in.calls[-2][1]["thread_id"] == claim_of(client, claim_id)["thread_id"]
    refusal = model.prompts[-2][-1].text
    assert refusal.startswith("Refused with status 422: ")
    assert "Claims manual section 14" in refusal
    steps = steps_of(client, claim_id)
    calls = [
        step["content"].split("(", 1)[0]
        for step in steps
        if step["kind"] == "tool_call"
    ]
    assert calls == [name for name, _ in stand_in.calls]
    [rejected] = [step for step in steps if step["kind"] == "refusal"]
    assert rejected["content"]["source"] == "App"
    assert rejected["content"]["reason"].startswith("Claims manual section 14")
    search, read = [step["content"] for step in steps if step["kind"] == "lookup"]
    assert {source["file"] for source in search["sources"]} == {
        "policy_wording.md",
        "claims_manual.md",
    }
    assert [source["file"] for source in read["sources"]] == ["policy_wording.md"]
    assert [name for name, _ in stand_in.calls] == [
        f"undercover-{target}___{operation}"
        for target, operation in (
            ("app", "get_claim"),
            ("app", "get_policy"),
            ("app", "get_claim_history"),
            ("context", "search_documents"),
            ("context", "read_section"),
            ("app", "record_decision"),
            ("app", "calculate_payout"),
            ("app", "record_decision"),
            ("app", "pay_claim"),
        )
    ]
    assert [arguments for _, arguments in stand_in.calls[:3]] == [
        {"claim_id": claim_id},
        {"policy_id": "POL-2001"},
        {"customer_id": "CUS-3001"},
    ]


def test_gateway_delivers_a_context_refusal_to_the_model(stand_in):
    model = stub(
        call("read_section", document="claims_manual", number=15),
        record("refer", 0),
    )
    assert "refer" in harness.work_claim("CLM-1001", graph_on(model))
    assert "Refused: Section 15 of the claims manual not found" in (
        model.prompts[-1][-1].text
    )
    assert stand_in.calls[0][0] == "undercover-context___read_section"


def test_screening_reads_the_same_fields_and_refers_on_the_direct_route(
    client, stand_in, monkeypatch, screen
):
    claim_id = "CLM-1901"
    claim = claim_of(client, claim_id)
    monkeypatch.setattr(screening, "check", screen)

    def flagged(messages):
        checked = json.loads(messages[-1].text)
        assert checked == {key: claim[key] for key in screening.CUSTOMER_FIELDS}
        return {
            "raw": None,
            "parsed": screening.Screening(reason="Flag found", instructions_found=True),
            "parsing_error": None,
        }

    monkeypatch.setattr(screening, "checker", lambda: RunnableLambda(flagged))
    harness.work_claim(claim_id, graph_on(stub()))
    assert stand_in.calls == []
    decision = claim_of(client, claim_id)["decision"]
    assert decision["section"] == decision["decided_by"] == screening.SECTION
    assert decision["payout"] == 0
    assert decision["calculation"] is None
    assert decision["manual_version"] == "2026.2"


@pytest.mark.parametrize("evaluation", [False, True])
def test_gateway_refusal_and_payment_hold(client, stand_in, evaluation):
    with engine().begin() as db:
        db.execute(update(settings).values(payout_approval_limit=2000))
    run = (
        api.start_run(
            {
                "kind": "evaluation",
                "set_name": "evaluation",
                "tiny": True,
                "concurrency": 1,
                "configuration_version": 1,
                "round_id": None,
            }
        )
        if evaluation
        else None
    )
    claim_id = run["members"][0]["claim_id"] if run else "CLM-1002"
    model = stub(
        call("get_policy"), calculate_for_claim(claim_id, 1350), record("approve", 1350)
    )
    graph = graph_on(model)
    harness.work_claim(claim_id, graph, run_id=run["run_id"] if run else None)
    held = claim_of(client, claim_id)
    assert held["status"] == "waiting_for_sign_off"
    assert held["payment"] is None
    assert held["decision"]["gateway_hold"] == DENIAL
    assert api.hold_gateway_payment(claim_id, DENIAL) == held
    refusals = [
        step for step in steps_of(client, claim_id) if step["kind"] == "refusal"
    ]
    assert len(refusals) == (1 if evaluation else 2)
    assert all(
        step["content"] == {"source": "Gateway", "reason": DENIAL} for step in refusals
    )
    signed = sign_off(client, claim_id, "HND-01")
    checkpoint = graph.get_state(harness.thread_of(held))
    if evaluation:
        assert signed.status_code == 409
        assert checkpoint.interrupts == checkpoint.next == ()
    else:
        assert model.prompts[-2][-1].text == f"Gateway refused: {DENIAL}"
        assert checkpoint.next == (harness.PAUSE,)
        assert len(checkpoint.interrupts) == 1
        assert signed.json()["payment"]["signed_off_by"] == "Dana Whitfield"
        decision = signed.json()["decision"]
        assert decision["calculation"] == held["decision"]["calculation"]
        assert decision["decided_by"] == held["decision"]["decided_by"]
        assert decision["manual_version"] == held["decision"]["manual_version"]
        assert sign_off(client, claim_id, "HND-01").status_code == 409


def test_calls_preserve_the_loop_used_by_the_lambda_handler(stand_in):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        via = gateway.configured()
        for _ in range(2):
            assert api.get_claim("CLM-1001", via=via)["claim_id"] == "CLM-1001"
        assert asyncio.get_event_loop() is loop
        path = "/api/health"
        event = {
            "version": "2.0",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {"host": "local-function.invalid"},
            "requestContext": {
                "http": {"method": "GET", "path": path, "sourceIp": "127.0.0.1"}
            },
        }
        assert handler(event, cast(LambdaContext, object()))["statusCode"] == 200
    finally:
        asyncio.set_event_loop(None)
        loop.close()
