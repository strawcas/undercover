"""One planning agent works a finite, backend-enforced claim scope."""

import json
from typing import Any
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelCallLimitMiddleware,
    hook_config,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime

from agent.execution import Execution, QueueStopped, Record, Runner
from agent.http import request as api_request
from agent.main import AgentRunError, backend, model, run, validate_model_config
from agent.middleware import ProviderRetry
from agent.queue_tools import QueueTools


class CompletionMiddleware(AgentMiddleware):
    """Prevent a final reply from hiding unfinished work or ignoring shutdown."""

    def __init__(self, tools: QueueTools):
        self.queue_tools = tools

    def before_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, Any] | None:
        if self.queue_tools.execution.stopping.is_set():
            raise QueueStopped
        history = state["messages"]
        snapshots = [
            message
            for message in history
            if isinstance(message, ToolMessage)
            and message.name == "list_waiting_claims"
        ]
        if len(snapshots) > 1:
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
            if removed:
                return {"messages": removed}
        return None

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and not last.tool_calls:
            check = self.queue_tools.check()
            if not check["complete"]:
                return {
                    "messages": [
                        HumanMessage(
                            content="The backend found unfinished work. "
                            "Continue using the tools: " + json.dumps(check),
                        )
                    ],
                    "jump_to": "model",
                }
        return None


def run_queue(
    scope: Record,
    runner: Runner = run,
    llm: BaseChatModel | None = None,
    execution: Execution | None = None,
) -> Record:
    """Coordinate a finite workload and preserve a factual final run record."""
    context = execution or Execution()
    with backend() as client:
        context.run_id = str(uuid4())
        response = api_request(
            client,
            "POST",
            "/api/agent-runs",
            {"run_id": context.run_id, "scope": scope},
        )
        if response.is_error:
            detail = response.json().get("detail")
            raise AgentRunError(
                detail
                if isinstance(detail, str)
                else "The backend rejected the run scope."
            )
        response.raise_for_status()
        registered = response.json()
        context.run_id = registered["run_id"]
        tools = QueueTools(client, registered["run_id"], runner, context)
        status, detail = "completed", ""
        try:
            if registered["claim_limit"]:
                try:
                    validate_model_config()
                except ValueError as failure:
                    raise AgentRunError(str(failure)) from failure
                prompt = tools.request("GET", "/api/agent-instructions")["queue_text"]
                budget = 10 + 10 * registered["claim_limit"]
                middleware: list[AgentMiddleware[Any, Any, Any]] = [
                    CompletionMiddleware(tools),
                    ProviderRetry(),
                    ModelCallLimitMiddleware(run_limit=budget, exit_behavior="error"),
                ]
                agent = create_agent(
                    llm or model(),
                    tools=tools.tools(),
                    system_prompt=prompt,
                    middleware=middleware,
                )
                agent.invoke(
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": f"Work registered run {context.run_id}. "
                                "Choose the order, maintain the plan, "
                                "delegate helpers and check completion.",
                            }
                        ]
                    },
                    config={
                        "configurable": {"thread_id": context.run_id},
                        "recursion_limit": budget * 10,
                    },
                )
                if not tools.check()["complete"]:
                    raise AgentRunError("Queue agent ended with unfinished work.")
        except QueueStopped:
            status, detail = "interrupted", "Queue processing was interrupted."
        except KeyboardInterrupt:
            status, detail = "interrupted", "Queue processing was interrupted."
            raise
        except AgentRunError as failure:
            status, detail = "failed", str(failure)
            raise
        except Exception as failure:
            status = "failed"
            detail = (
                f"Queue agent failed ({type(failure).__name__}). "
                "Check the model and backend."
            )
            raise AgentRunError(detail) from failure
        finally:
            if context.result is None:
                context.result = tools.request(
                    "POST", f"{tools.path}/finish", {"status": status, "detail": detail}
                )
    return context.result
