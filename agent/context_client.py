"""Use local context MCP or the Gateway's context target."""

import os
from dataclasses import dataclass
from typing import Any, Literal

from agent import gateway
from agent.mcp_client import McpClient

DEFAULT_URL = "http://127.0.0.1:8006/mcp"
DocumentName = Literal["policy_wording", "claims_manual"]
RecordName = Literal["claim", "policy", "previous_claim"]
PlanName = Literal["economy", "premium", "first"]


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
