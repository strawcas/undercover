"""Expose the context layer's MCP schemas through the synchronous graph."""

import json
from collections.abc import Callable
from typing import Any

import httpx
from langchain.tools import ToolException
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StreamableHttpConnection

from agent import context_client, gateway, mcp_client
from agent.tools import CONTEXT_TOOLS

CONTEXT_NAMES = {tool.name for tool in CONTEXT_TOOLS}


def content(message: ToolMessage) -> Any:
    """Read structured MCP results and keep provenance with the content."""
    if message.status == "error":
        raise ToolException(message.text)
    artifact = message.artifact
    if isinstance(artifact, dict) and "structured_content" in artifact:
        return artifact["structured_content"]
    try:
        return json.loads(message.text)
    except ValueError:
        return message.text


def synchronous_tool(source: BaseTool, name: str) -> StructuredTool:
    """Adapt the MCP tool's schema and result for the blocking ToolNode."""

    def invoke(**arguments: Any) -> Any:
        try:
            message = mcp_client.synchronous(
                source.ainvoke(
                    {
                        "type": "tool_call",
                        "name": source.name,
                        "args": arguments,
                        "id": "context",
                    }
                )
            )
        except (OSError, ExceptionGroup, httpx.RequestError) as error:
            raise ToolException("The context service is unavailable.") from error
        return content(message)

    return StructuredTool.from_function(
        func=invoke,
        name=name,
        description=source.description,
        args_schema=source.args_schema,
        infer_schema=False,
    )


def expose(via: gateway.Gateway | None) -> list[BaseTool]:
    """Discover and allow only the seven known context tools on either route."""
    route = context_client.configured(via).endpoint
    endpoint = route.client if isinstance(route, gateway.Gateway) else route
    connection: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": endpoint.url,
    }
    if endpoint.auth is not None:
        connection["auth"] = endpoint.auth
    client = MultiServerMCPClient({"context": connection})
    try:
        available = mcp_client.synchronous(client.get_tools(server_name="context"))
    except (OSError, ExceptionGroup, httpx.RequestError) as error:
        return unavailable(error)
    by_name = {source.name: source for source in available}
    native: Callable[[str], str] = (
        (lambda name: gateway.tool_name(gateway.CONTEXT, name)) if via else str
    )
    return [
        synchronous_tool(by_name[native(name)], name)
        for name in sorted(CONTEXT_NAMES)
        if native(name) in by_name
    ]


def unavailable(error: Exception) -> list[BaseTool]:
    """Keep the handler's app tools usable when context discovery fails."""

    def refuse(**_: Any) -> Any:
        raise ToolException("The context service is unavailable.") from error

    return [
        StructuredTool.from_function(
            func=refuse,
            name=source.name,
            description=source.description,
            args_schema=source.tool_call_schema,
            infer_schema=False,
        )
        for source in CONTEXT_TOOLS
    ]
