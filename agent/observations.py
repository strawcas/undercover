"""Record the claim agent's calls, results and refusals for handlers."""

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from agent import api, gateway
from agent.tools import CLAIM_TOOLS, CONTEXT_TOOLS, ClaimContext, search_memory

GATEWAY_REFUSAL = "Gateway refused: "
APP_REFUSAL = "Refused with status "
TOOL_REFUSAL = "Refused: "
CONTEXT_OPERATIONS = {tool.name for tool in CONTEXT_TOOLS}
APP_OPERATIONS = {tool.name for tool in CLAIM_TOOLS} | {"pay_claim"}


def display_name(operation: str, via: gateway.Gateway | None) -> str:
    """Name the actual tool invoked, including the Gateway target when routed."""
    if via is not None:
        if operation in CONTEXT_OPERATIONS:
            return gateway.tool_name(gateway.CONTEXT, operation)
        if operation in APP_OPERATIONS:
            return gateway.tool_name(gateway.APP, operation)
    return operation


def record_step(
    context: ClaimContext, kind: api.StepKind, value: api.StepContent
) -> None:
    """Tie this observation to the attempt that made it."""
    api.record_step(context.claim_id, kind, value, thread_id=context.thread_id)


def record_call(
    context: ClaimContext,
    name: str,
    arguments: dict[str, Any],
    via: gateway.Gateway | None,
) -> None:
    """Record one tool call with its inputs."""
    inputs = ", ".join(f"{key}={json.dumps(value)}" for key, value in arguments.items())
    record_step(context, "tool_call", f"{display_name(name, via)}({inputs})")


def record_reply(
    context: ClaimContext, reply: BaseMessage, via: gateway.Gateway | None
) -> None:
    """Record a model message and name each tool it is about to call."""
    if reply.text:
        record_step(context, "message", reply.text)
    if isinstance(reply, AIMessage):
        for call in reply.tool_calls:
            record_call(context, call["name"], call["args"], via)


def record_refusal(context: ClaimContext, operation: str, text: str) -> None:
    """Identify which system refused a call and keep the reason."""
    if text.startswith(GATEWAY_REFUSAL):
        source = "Gateway"
        text = text.removeprefix(GATEWAY_REFUSAL)
    elif text.startswith(APP_REFUSAL):
        source = "App"
        _, _, text = text.removeprefix(APP_REFUSAL).partition(": ")
    elif operation in CONTEXT_OPERATIONS:
        source = "Context layer"
    elif operation == search_memory.name:
        source = "Customer memory"
    else:
        source = "Tool"
    record_step(
        context,
        "refusal",
        {"source": source, "reason": text.removeprefix(TOOL_REFUSAL)},
    )


def record_tool_result(
    context: ClaimContext,
    name: str,
    message: ToolMessage,
    via: gateway.Gateway | None,
) -> None:
    """Record the result and any structured observation from a tool."""
    if message.status == "error":
        record_refusal(context, name, message.text)
    else:
        display = display_name(name, via)
        record_step(context, "tool_result", f"{display}: {message.text}")
    if message.artifact:
        record_step(context, *message.artifact)
