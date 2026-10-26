"""Local execution state shared with the queue's HTTP tools."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event
from time import monotonic
from typing import Any

Record = dict[str, Any]
Runner = Callable[[str, str], str]


def ignore_result(_result: Record) -> None:
    """Leave reporting to the dashboard when no terminal observer is attached."""


@dataclass
class Execution:
    stopping: Event = field(default_factory=Event)
    cancel_active: Event = field(default_factory=Event)
    on_result: Callable[[Record], None] = ignore_result
    result: Record | None = None
    run_id: str | None = None


class QueueStopped(Exception):
    """Stop admitting work when the owning process shuts down."""


class AgentRunError(Exception):
    """An actionable failure safe to show in claim activity."""


attempt_budget: ContextVar[tuple[float, Event] | None] = ContextVar(
    "attempt_budget", default=None
)
DEFAULT_ATTEMPT_SECONDS = 600


def check_attempt() -> None:
    budget = attempt_budget.get()
    if budget is not None:
        deadline, cancelled = budget
        if cancelled.is_set():
            raise QueueStopped
        if monotonic() >= deadline:
            raise AgentRunError("Assessment deadline exceeded.")


@contextmanager
def default_budget():
    token = attempt_budget.set(
        attempt_budget.get() or (monotonic() + DEFAULT_ATTEMPT_SECONDS, Event())
    )
    try:
        yield
    finally:
        attempt_budget.reset(token)
