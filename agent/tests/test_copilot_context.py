import httpx
from langchain_core.tools import StructuredTool

from agent import gateway
from agent.copilot import context_tools, progress
from agent.mcp_client import McpClient


def test_direct_and_signed_tools_use_plain_names_and_keep_structured_sources(
    monkeypatch,
):
    auth = httpx.Auth()

    async def lookup(**_arguments):
        return "Plain text without provenance", {
            "structured_content": {
                "source": {"file": "plans.yaml", "owner": "Product"},
                "body": "A" * 6500,
            }
        }

    class Server:
        def __init__(self, connections):
            self.connection = connections["context"]

        async def get_tools(self, *, server_name):
            assert server_name == "context"
            signed = self.connection.get("auth") is auth
            names = [
                gateway.tool_name(gateway.CONTEXT, name) if signed else name
                for name in context_tools.CONTEXT_NAMES
            ]
            if signed:
                names.append(gateway.tool_name(gateway.APP, "record_decision"))
            return [
                StructuredTool.from_function(
                    coroutine=lookup,
                    name=name,
                    description="Read a record",
                    args_schema={"type": "object", "properties": {}},
                    response_format="content_and_artifact",
                )
                for name in names
            ]

    monkeypatch.setattr(context_tools, "MultiServerMCPClient", Server)
    via = gateway.Gateway(McpClient("https://gateway.invalid/mcp", auth))
    for route in (None, via):
        tools = context_tools.expose(route)
        assert {tool.name for tool in tools} == context_tools.CONTEXT_NAMES
        value = next(tool for tool in tools if tool.name == "get_plan").invoke({})
        assert progress.bounded(value, "")["sources"] == [
            {"file": "plans.yaml", "owner": "Product"}
        ]
