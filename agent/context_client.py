"""Use local context MCP or the Gateway's context target."""

import os
from dataclasses import dataclass
from typing import Any

from agent import gateway
from agent.mcp_client import McpClient
from context.endpoint import DEFAULT_URL


@dataclass(frozen=True)
class ContextClient:
    endpoint: McpClient | gateway.Gateway

    def call(self, operation: str, **arguments: Any) -> dict[str, Any]:
        """Read a context tool's answer with its provenance."""
        if isinstance(self.endpoint, gateway.Gateway):
            return self.endpoint.call(gateway.CONTEXT, operation, **arguments)
        return self.endpoint.call(operation, arguments)


def configured(via: gateway.Gateway | None) -> ContextClient:
    """Resolve the context route at claim graph construction."""
    return ContextClient(
        via or McpClient(os.getenv("UNDERCOVER_CONTEXT") or DEFAULT_URL)
    )
