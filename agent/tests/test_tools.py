from agent.tools import CLAIM_TOOLS, CONTEXT_TOOLS


def operations(client):
    """Return the operation ids of the backend's API."""
    paths = client.get("/openapi.json").json()["paths"].values()
    return {operation["operationId"] for path in paths for operation in path.values()}


def test_claim_tools_are_named_after_backend_operations(client):
    names = [t.name for t in CLAIM_TOOLS]
    assert names == [
        "get_claim",
        "get_policy",
        "get_claim_history",
        "calculate_payout",
        "record_decision",
    ]
    assert set(names) <= operations(client)
    assert [tool.name for tool in CONTEXT_TOOLS] == [
        "lookup_term",
        "describe_fields",
        "get_plan",
        "get_claim_rules",
        "search_reference_data",
        "search_documents",
        "read_section",
    ]
    assert operations(client).isdisjoint(tool.name for tool in CONTEXT_TOOLS)


def test_a_claim_agent_can_only_act_on_its_own_claim():
    ids = {"claim_id", "policy_id", "customer_id"}
    assert all(ids.isdisjoint(t.args) for t in CLAIM_TOOLS)
