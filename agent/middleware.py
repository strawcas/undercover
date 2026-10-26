"""Provider retries and per-attempt limits inside LangChain's existing agent."""

from collections.abc import Callable
from typing import Any

import httpx
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
    ModelRetryMiddleware,
)
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from ollama import ResponseError
from openai import APIConnectionError, APIStatusError

from agent.execution import check_attempt
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
    def __init__(self, on_retry: Callable[[int], None] | None = None):
        super().__init__(
            max_retries=MAX_RETRIES,
            retry_on=transient_failure,
            on_failure="error",
            initial_delay=0.25,
            max_delay=0.5,
            jitter=False,
        )
        self.on_retry = on_retry

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse | AIMessage:
        calls = 0

        def observed(current: ModelRequest) -> ModelResponse:
            nonlocal calls
            check_attempt()
            if calls and self.on_retry is not None:
                self.on_retry(calls)
            calls += 1
            return handler(current)

        return super().wrap_model_call(request, observed)


class ClaimBudget(AgentMiddleware):
    def before_model(self, state: AgentState, runtime: Runtime) -> None:
        check_attempt()


def claim_middleware(
    on_retry: Callable[[int], None],
) -> list[AgentMiddleware[Any, Any, Any]]:
    return [
        ClaimBudget(),
        ProviderRetry(on_retry),
        ModelCallLimitMiddleware(run_limit=40, exit_behavior="error"),
    ]
