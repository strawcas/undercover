"""Check the MCP contract and the Lambda transport with the real context files."""

import asyncio
import json
from pathlib import Path
from typing import cast

import pytest
from mangum.types import LambdaContext
from starlette.testclient import TestClient

from context.catalog import ROOT
from context.server import build_server, handler

HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}
HOST = "context-id.lambda-url.us-east-1.on.aws"


def rpc(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


@pytest.fixture
def mcp():
    with TestClient(
        build_server().streamable_http_app(), base_url="http://127.0.0.1:8006"
    ) as client:

        def send(method, **params):
            response = client.post("/mcp", headers=HEADERS, json=rpc(method, **params))
            assert response.status_code == 200, response.text
            return response.json()["result"]

        yield send


def call(mcp, name, **arguments):
    result = mcp("tools/call", name=name, arguments=arguments)
    assert not result["isError"]
    return result["structuredContent"]


def test_lists_read_only_tools_with_required_arguments(mcp):
    expected = {
        "lookup_term": {"term"},
        "describe_fields": {"record"},
        "get_plan": {"plan"},
        "get_claim_rules": set(),
        "search_reference_data": {"query"},
        "search_documents": {"query"},
        "read_section": {"document", "number"},
    }
    tools = mcp("tools/list")["tools"]
    assert {tool["name"] for tool in tools} == set(expected)
    for tool in tools:
        assert tool["description"]
        assert "\n" not in tool["description"]
        assert tool["annotations"]["readOnlyHint"]
        assert set(tool["inputSchema"].get("required", [])) == expected[tool["name"]]


def test_reads_yaml_and_reports_provenance(mcp):
    term = call(mcp, "lookup_term", term="UnAtTeNdEd")
    assert term["term"] == "Unattended"
    assert term["sections"] == [5]
    assert term["source"] == {
        "file": "glossary.yaml",
        "system": "The policy wording, version 2026.1",
        "owner": "Legal",
        "reviewed": "2026-09-01",
    }
    assert "Unattended" in call(mcp, "lookup_term", term="unknown")["known_terms"]
    for record in ("claim", "policy", "previous_claim"):
        fields = call(mcp, "describe_fields", record=record)
        assert fields["record"] == record
        assert fields["fields"]
        assert fields["source"]["file"] == "data_dictionary.yaml"
    for name, deductible in (("economy", 100), ("premium", 50), ("first", 0)):
        plan = call(mcp, "get_plan", plan=name)
        assert plan["deductible"] == deductible
        assert plan["add_ons"]
        assert plan["deductible_applies_to"]
        assert plan["source"]["file"] == "plans.yaml"
    rules = call(mcp, "get_claim_rules")
    assert len(rules["rules"]) == 4
    assert rules["source"]["file"] == "claim_rules.yaml"


def test_reference_search_matches_all_words_case_insensitively(mcp):
    result = call(mcp, "search_reference_data", query="STRIKE LIsbon")
    assert result["source"]["file"] == "reference_data.yaml"
    [match] = result["matches"]
    assert match["topic"] == "events"
    assert "48 hour strike" in match["fact"]
    assert call(mcp, "search_reference_data", query="Lisbon zeppelin")["matches"] == []


def test_numbered_documents_and_search_keep_their_sources(mcp):
    manual = call(mcp, "read_section", document="claims_manual", number=14)
    assert (manual["document"], manual["number"], manual["title"]) == (
        "claims_manual",
        14,
        "Records",
    )
    assert manual["text"].startswith("Every decision record")
    assert manual["source"]["owner"] == "Head of claims operations"
    wording = call(mcp, "read_section", document="policy_wording", number=3)
    assert wording["title"] == "Trip Delay"
    assert "Baggage Delay" not in wording["text"]
    assert wording["source"]["file"] == "policy_wording.md"
    found = call(mcp, "search_documents", query="DeDUCTIBLE")["matches"]
    assert {hit["source"]["file"] for hit in found} == {
        "policy_wording.md",
        "claims_manual.md",
    }
    assert all(hit["excerpts"] or "deductible" in hit["title"].lower() for hit in found)
    assert call(mcp, "search_documents", query="zeppelin")["matches"] == []
    missing = mcp(
        "tools/call",
        name="read_section",
        arguments={"document": "claims_manual", "number": 15},
    )
    assert missing["isError"]
    assert "Section 15 of the claims manual not found" in missing["content"][0]["text"]


def invoke(message):
    event = {
        "version": "2.0",
        "rawPath": "/mcp",
        "rawQueryString": "",
        "headers": {**HEADERS, "host": HOST},
        "requestContext": {
            "domainName": HOST,
            "http": {"method": "POST", "path": "/mcp", "sourceIp": "127.0.0.1"},
        },
        "body": json.dumps(message),
        "isBase64Encoded": False,
    }
    response = handler(event, cast(LambdaContext, object()))
    assert response["statusCode"] == 200, response
    return json.loads(response["body"])["result"]


def test_function_url_handles_multiple_invocations_without_reloading_docs(monkeypatch):
    read_text = Path.read_text

    def reread(path, *args, **kwargs):
        if path.parent == ROOT:
            pytest.fail("The context service re-read a governance file")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reread)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        assert len(invoke(rpc("tools/list"))["tools"]) == 7
        result = invoke(
            rpc("tools/call", name="lookup_term", arguments={"term": "carrier"})
        )
        assert result["structuredContent"]["term"] == "Carrier"
    finally:
        asyncio.set_event_loop(None)
        loop.close()
