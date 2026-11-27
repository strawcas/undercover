"""Call a streamable HTTP MCP tool from the synchronous claim graph."""

import asyncio
import json
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

import httpx
from langchain.tools import ToolException
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, TextContent


def synchronous[T](operation: Coroutine[Any, Any, T]) -> T:
    """Run one asynchronous MCP operation in the synchronous worker."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(operation)
    finally:
        loop.close()


@dataclass(frozen=True)
class McpClient:
    """An MCP endpoint and its optional HTTP authentication."""

    url: str
    auth: httpx.Auth | None = None

    async def _call(
        self, name: str, arguments: dict[str, Any]
    ) -> CallToolResult | McpError:
        async with (
            httpx.AsyncClient(auth=self.auth, timeout=30) as client,
            streamable_http_client(self.url, http_client=client) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            try:
                return await session.call_tool(name, arguments)
            except McpError as error:
                return error

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Return a tool's JSON or propagate its refusal."""
        result = synchronous(self._call(name, arguments))
        if isinstance(result, McpError):
            raise result

        text = "\n".join(
            part.text for part in result.content if isinstance(part, TextContent)
        )
        if result.isError:
            raise ToolException(text)
        if result.structuredContent is not None:
            return result.structuredContent
        return json.loads(text)
