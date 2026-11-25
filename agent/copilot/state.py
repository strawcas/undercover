"""Public checkpoint state and request-only context for the Copilot graph."""

from dataclasses import dataclass
from typing import Any

from langgraph.graph import MessagesState

from agent.copilot.api import Progress


class State(MessagesState, total=False):
    calls: int
    cost: float | None
    intent: dict[str, Any] | None
    confirmed: bool
    criteria: str | None
    seconds: float
    model: str


@dataclass(frozen=True)
class Context:
    progress: Progress
    handler: dict[str, Any]
