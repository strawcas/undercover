"""The synchronous LangGraph loop for a handler's turn."""

import json
import re
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any, cast

from langchain.tools import ToolException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent import measurements
from agent.copilot import actions, progress
from agent.copilot.prompt import INSTRUCTIONS
from agent.copilot.state import Context, State

MAX_CALLS = 25
MODEL = "model_reply"
TOOLS = "run_tools"
CONFIRM = "confirm_sign_off"
SETTLE = "settle_sign_off"
EXHAUSTED = (
    "I reached the turn’s model call limit. What would you like me to check next?"
)
ToolResult = ToolMessage | Command
CopilotGraph = CompiledStateGraph[State, Context, Any, Any]


def model_node(model: Any, state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    """Ask the model once for the next action or the final answer."""
    handler = runtime.context.handler
    authority = "can" if handler["can_approve_over_limit"] else "cannot"
    identity = (
        f"Verified handler: {handler['name']}. They {authority} sign off payouts."
    )
    reply: AIMessage = model.invoke(
        [SystemMessage(INSTRUCTIONS + "\n" + identity), *state["messages"]]
    )
    cost = measurements.model_reply(reply, None)["cost"]
    total = state.get("cost")
    calls = state.get("calls", 0) + 1
    if calls == MAX_CALLS and reply.tool_calls:
        reply = AIMessage(EXHAUSTED)
    for index, call in enumerate(reply.tool_calls):
        if call["name"] == actions.PREPARE:
            reply = reply.model_copy(
                update={"tool_calls": reply.tool_calls[: index + 1]}
            )
            break
    if not reply.tool_calls and (assessment := state.get("criteria")):
        reply = AIMessage(assessment_answer(reply.text, assessment))
    return {
        "messages": [reply],
        "calls": calls,
        "cost": total + cost if total is not None and cost is not None else None,
    }


def assessment_answer(prose: str, assessment: str) -> str:
    """Keep a short, non-quantitative opener above the authoritative results."""
    opener = prose.splitlines()[0].strip() if prose else ""
    if re.fullmatch(r"(?:Here (?:is|are)|I checked) [A-Za-z ’'-]{4,85}[.!]", opener):
        return f"{opener}\n\n{assessment}"
    return assessment


def next_node(state: State) -> str:
    """Continue only when a reply requests tools."""
    newest = state["messages"][-1]
    return TOOLS if isinstance(newest, AIMessage) and newest.tool_calls else END


def after_tools(state: State) -> str:
    """Wait for a handler only when a tool saved a sign-off intent."""
    return CONFIRM if state.get("intent") else MODEL


def report(
    kinds: dict[str, str],
    request: ToolCallRequest,
    execute: Callable[[ToolCallRequest], ToolResult],
) -> ToolResult:
    """Report each call with the public input and result only."""
    name = request.tool_call["name"]
    context = cast(Context, request.runtime.context)
    output: list[ToolResult] = []

    def execute_tool() -> Any:
        result = execute(request)
        output.append(result)
        if isinstance(result, Command) and isinstance(result.update, dict):
            for message in result.update.get("messages", []):
                if isinstance(message, ToolMessage):
                    return message.text
            return None
        if isinstance(result, Command):
            return None
        if result.status == "error":
            raise ToolException(result.text)
        try:
            return json.loads(result.text)
        except ValueError:
            return result.text

    try:
        progress.observe(
            context.progress,
            name,
            kinds.get(name, "context"),
            json.dumps(request.tool_call["args"], ensure_ascii=False),
            execute_tool,
        )
    except ToolException as error:
        return ToolMessage(
            str(error), name=name, tool_call_id=request.tool_call["id"], status="error"
        )
    return output[0]


def build(
    model: BaseChatModel,
    saver: BaseCheckpointSaver,
    tools: Sequence[tuple[BaseTool, str]],
) -> CopilotGraph:
    """Connect the model and ordered ToolNode with durable turn state."""
    operations = [tool for tool, _ in tools]
    kinds = {tool.name: kind for tool, kind in tools}
    flow = StateGraph(State, context_schema=Context)
    flow.add_node(MODEL, partial(model_node, model.bind_tools(operations)))
    flow.add_node(
        TOOLS,
        ToolNode(operations, wrap_tool_call=partial(report, kinds)),
    )
    flow.add_edge(START, MODEL)
    flow.add_node(CONFIRM, actions.confirm)
    flow.add_node(SETTLE, actions.settle)
    flow.add_conditional_edges(MODEL, next_node, [TOOLS, END])
    flow.add_conditional_edges(TOOLS, after_tools, [CONFIRM, MODEL])
    flow.add_edge(CONFIRM, SETTLE)
    flow.add_edge(SETTLE, END)
    return flow.compile(checkpointer=saver)


def config(turn_id: str) -> RunnableConfig:
    """Name this turn's checkpoint thread with no request secrets."""
    return {
        "configurable": {"thread_id": f"copilot:{turn_id}"},
        "max_concurrency": 1,
        "recursion_limit": (MAX_CALLS * 2) + 10,
    }


def earlier_messages(graph: CopilotGraph, turn_id: str | None) -> list[BaseMessage]:
    """Load the prior completed thread's evidence if it still exists."""
    if turn_id is None:
        return []
    return graph.get_state(config(turn_id)).values.get("messages", [])
