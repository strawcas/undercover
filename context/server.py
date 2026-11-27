"""Serve the UnderCover context catalog over MCP locally or on Lambda."""

from typing import Any

from mangum import Mangum
from mangum.types import LambdaContext, LambdaEvent
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from context.endpoint import HOST, MCP_PATH, PORT
from context.tools import TOOLS


def build_server(*, function_url: bool = False) -> FastMCP:
    """Create one stateless MCP server over the process's loaded tools."""
    transport_security = (
        TransportSecuritySettings(enable_dns_rebinding_protection=False)
        if function_url
        else None
    )
    server = FastMCP(
        "UnderCover context",
        host=HOST,
        port=PORT,
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
    )
    for tool in TOOLS:
        server.add_tool(
            tool, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False)
        )
    return server


def handler(event: LambdaEvent, context: LambdaContext) -> dict[str, Any]:
    """Serve one IAM-protected Function URL invocation."""
    server = build_server(function_url=True)
    return Mangum(server.streamable_http_app(), lifespan="auto")(event, context)
