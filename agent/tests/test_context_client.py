"""Exercise the direct tool route over unsigned streamable HTTP."""

import httpx
from fastapi.testclient import TestClient

from agent import context_client, harness, mcp_client
from agent.tests.stubs import call, graph_on, record, stub
from context.endpoint import DEFAULT_URL
from context.server import build_server


def test_direct_context_endpoint_is_resolved_at_graph_construction(
    monkeypatch, local_tool_settings
):
    monkeypatch.setattr(mcp_client.McpClient, "call", local_tool_settings)
    endpoint = context_client.configured(None).endpoint
    assert isinstance(endpoint, mcp_client.McpClient)
    assert endpoint.url == DEFAULT_URL
    url = "http://localhost:8007/mcp"
    monkeypatch.setenv("UNDERCOVER_CONTEXT", url)
    real_client = httpx.AsyncClient
    with TestClient(
        build_server().streamable_http_app(), base_url=url.removesuffix("/mcp")
    ) as service:

        def respond(request):
            assert str(request.url) == url
            assert "authorization" not in request.headers
            reply = service.request(
                request.method, "/mcp", headers=request.headers, content=request.content
            )
            return httpx.Response(
                reply.status_code, headers=reply.headers, content=reply.content
            )

        monkeypatch.setattr(
            mcp_client.httpx,
            "AsyncClient",
            lambda **options: real_client(
                transport=httpx.MockTransport(respond), **options
            ),
        )
        model = stub(call("lookup_term", term="Carrier"), record("refer", 0))
        graph = graph_on(model)
        monkeypatch.delenv("UNDERCOVER_CONTEXT")
        assert "refer" in harness.work_claim("CLM-1001", graph)
        assert '"term": "Carrier"' in model.prompts[-1][-1].text
        assert "glossary.yaml" in model.prompts[-1][-1].text
