"""Local execution state shared with the queue's HTTP tools."""

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event
from time import monotonic
from typing import Protocol

from backend.claim_models import ClaimDetail
from backend.harness_models import Attempt, Run


@dataclass(frozen=True)
class AttemptResult:
    attempt: Attempt
    claim: ClaimDetail


def ignore_result(_result: AttemptResult) -> None:
    """Leave reporting to the dashboard when no terminal observer is attached."""


@dataclass
class Execution:
    stopping: Event = field(default_factory=Event)
    cancel_active: Event = field(default_factory=Event)
    on_result: Callable[[AttemptResult], None] = ignore_result
    result: Run | None = None
    run_id: str | None = None


class QueueStopped(Exception):
    """Stop admitting work when the owning process shuts down."""


class AgentRunError(Exception):
    """An actionable failure safe to show in claim activity."""


DEFAULT_ATTEMPT_SECONDS = 600


@dataclass
class AttemptBudget:
    deadline: float = field(
        default_factory=lambda: monotonic() + DEFAULT_ATTEMPT_SECONDS
    )
    cancelled: Event = field(default_factory=Event)
    model_calls: int = 40
    tool_calls: int = 80

    def check(self) -> None:
        if self.cancelled.is_set():
            raise QueueStopped
        if monotonic() >= self.deadline:
            raise AgentRunError("Assessment deadline exceeded.")


class Runner(Protocol):
    def __call__(
        self, claim_id: str, thread_id: str, /, *, budget: AttemptBudget
    ) -> str: ...
