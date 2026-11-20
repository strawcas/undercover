"""Answer context calls from the same read-only catalog used by the service."""

from collections.abc import Callable
from typing import Any

from langchain.tools import ToolException
from mcp.server.fastmcp.exceptions import ToolError

from context.tools import TOOLS

OPERATIONS: dict[str, Callable[..., dict[str, Any]]] = {
    tool.__name__: tool for tool in TOOLS
}


class ContextStandIn:
    def __init__(self, url: str) -> None:
        self.url = url

    def call(self, name: str, arguments: dict) -> dict[str, Any]:
        try:
            return OPERATIONS[name](**arguments)
        except ToolError as error:
            raise ToolException(str(error)) from error
