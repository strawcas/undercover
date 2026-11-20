"""The Gateway imports only the six claim operations and their schemas."""

from fastapi.testclient import TestClient

from api_rules import references, rule_breaks
from backend.main import app
from backend.tests.auth_support import AGENT_HEADERS, authorization


def test_agent_contract_is_scoped_and_authenticates_both_callers():
    routes = TestClient(app)
    path = "/api/agent/openapi.json"
    assert routes.get(path).status_code == 401
    response = routes.get(path, headers=AGENT_HEADERS)
    assert response.status_code == 200
    schema = response.json()
    assert routes.get(path, headers=authorization()).json() == schema
    assert "servers" not in schema
    assert "securitySchemes" not in schema["components"]
    assert set(references(schema)) <= set(schema["components"]["schemas"])
    assert rule_breaks(schema) == []
    operations = {
        operation["operationId"]: operation
        for methods in schema["paths"].values()
        for operation in methods.values()
    }
    assert set(operations) == {
        "get_claim",
        "get_policy",
        "get_claim_history",
        "calculate_payout",
        "record_decision",
        "pay_claim",
    }
    assert not any(
        p["name"].lower() == "x-api-key"
        for operation in operations.values()
        for p in operation.get("parameters", [])
    )
    [payment_body] = references(operations["pay_claim"]["requestBody"])
    assert schema["components"]["schemas"][payment_body]["required"] == ["amount"]
    [decision_body] = references(operations["record_decision"]["requestBody"])
    assert {"decided_by", "thread_id"} <= set(
        schema["components"]["schemas"][decision_body]["required"]
    )
    [payout_body] = references(operations["calculate_payout"]["requestBody"])
    assert "thread_id" in schema["components"]["schemas"][payout_body]["required"]
