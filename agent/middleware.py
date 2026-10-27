"""Provider retries and per-attempt limits inside LangChain's existing agent."""

from collections.abc import Callable
from threading import Event
from typing import Any

import httpx
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ModelRetryMiddleware,
)
from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime
from ollama import ResponseError
from openai import APIConnectionError, APIStatusError

from agent.execution import AgentRunError, AttemptBudget, QueueStopped
from agent.http import MAX_RETRIES, RETRY_STATUSES


def transient_failure(error: Exception) -> bool:
    if isinstance(error, (httpx.TransportError, APIConnectionError)):
        return True
    if isinstance(error, (httpx.HTTPStatusError, APIStatusError, ResponseError)):
        status = (
            error.response.status_code
            if isinstance(error, httpx.HTTPStatusError)
            else error.status_code
        )
        return status in RETRY_STATUSES
    return False


class ProviderRetry(ModelRetryMiddleware):
    def __init__(
        self,
        on_retry: Callable[[int], None] | None = None,
        budget: AttemptBudget | None = None,
    ):
        super().__init__(
            max_retries=MAX_RETRIES,
            retry_on=transient_failure,
            on_failure="error",
            initial_delay=0.25,
            max_delay=0.5,
            jitter=False,
        )
        self.on_retry = on_retry
        self.budget = budget

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse | AIMessage:
        calls = 0

        def observed(current: ModelRequest) -> ModelResponse:
            nonlocal calls
            if self.budget is not None:
                self.budget.check()
            if calls and self.on_retry is not None:
                self.on_retry(calls)
            calls += 1
            return handler(current)

        return super().wrap_model_call(request, observed)


class QueueContext(AgentMiddleware):
    """Bound framework history and calls across explicit completion continuations."""

    def __init__(self, stopping: Event, model_calls: int):
        self.stopping = stopping
        self.model_calls = model_calls
        self.calls = 0

    def before_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, Any] | None:
        if self.stopping.is_set():
            raise QueueStopped
        if self.calls >= self.model_calls:
            raise AgentRunError("Queue model-call limit exceeded.")
        self.calls += 1
        history = state["messages"]
        snapshots = [
            message
            for message in history
            if isinstance(message, ToolMessage)
            and message.name == "list_waiting_claims"
        ]
        if len(snapshots) <= 1:
            return None
        call_id = snapshots[-1].tool_call_id
        start = next(
            index
            for index, message in enumerate(history)
            if isinstance(message, AIMessage)
            and any(call["id"] == call_id for call in message.tool_calls)
        )
        removed = [
            RemoveMessage(id=message.id)
            for message in history[1:start]
            if message.id is not None
        ]
        return {"messages": removed} if removed else None
