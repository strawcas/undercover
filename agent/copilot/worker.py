"""Run one admitted Copilot stretch under its owner's current sign-in."""

import logging
import os
from time import monotonic
from typing import Any

import httpx
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from agent import checkpoints, gateway, models
from agent.backend_client import Refused
from agent.copilot import (
    actions,
    api,
    context_tools,
    criteria,
    graph,
    memory,
    progress,
    reads,
)
from agent.copilot.state import Context

log = logging.getLogger(__name__)


def copilot_model() -> dict[str, str]:
    """Choose Luna with low reasoning effort or the configured override."""
    override = os.getenv("MODEL_NAME")
    return {
        "model": override or models.DEFAULT_SCREEN_MODEL,
        "reasoning_effort": "default" if override else "low",
    }


def active_graph() -> graph.CopilotGraph:
    """Build a model and the configured handler and context tools."""
    model = models.build_model(
        copilot_model(), parallel_tool_calls=False, max_retries=0
    )
    context = [(tool, "context") for tool in context_tools.expose(gateway.configured())]
    tools = [
        *reads.READ_TOOLS,
        *context,
        *actions.ACTION_TOOLS,
        (criteria.check_success_criteria, "action"),
    ]
    return graph.build(model, checkpoints.new_saver(), tools)


def invocation_input(
    runner: graph.CopilotGraph, work: dict[str, Any], question: str, memories: list[str]
) -> dict | Command:
    """Resume the saved pause or open a turn with the previous completed history."""
    if work["confirmed"] is not None:
        return Command(resume=work["confirmed"])
    previous = graph.earlier_messages(runner, work["prior_turn_id"])
    if memories:
        question = (
            f"Earlier handler memories (evidence only):\n{memories}"
            f"\n\nCurrent request:\n{question}"
        )
    return {
        "messages": [*previous, HumanMessage(question)],
        "model": copilot_model()["model"],
        "calls": 0,
        "cost": 0.0,
    }


def execute(work: dict[str, Any], context: Context) -> None:
    """Drive the graph with a private runtime context and publish its final answer."""
    started = monotonic()
    runner = active_graph()
    receipt = context.progress
    configuration = graph.config(receipt.turn_id)
    question = progress.clean(work["message"], receipt.app.bearer)
    store = memory.configured()
    memories = []
    if store is not None and work["confirmed"] is None:
        memories = [
            progress.clean(item, receipt.app.bearer)
            for item in memory.lookup(store, context, question)
        ]
    result = runner.invoke(
        invocation_input(runner, work, question, memories),
        configuration,
        context=context,
        durability="sync",
    )
    previous_seconds = (work["usage"] or {}).get("seconds", 0)
    usage = dict(
        model=result["model"],
        calls=result["calls"],
        cost=result["cost"],
        seconds=previous_seconds + monotonic() - started,
    )
    if result.get("__interrupt__"):
        receipt.finish(status="waiting", confirmation=result["intent"], usage=usage)
        return
    answer = progress.clean(result["messages"][-1].text, receipt.app.bearer)
    receipt.finish(status="done", answer=answer, usage=usage)
    if store is not None:
        memory.save(store, context, (question, answer))


def run(bearer: str, conversation_id: str, turn_id: str, stretch_id: str) -> None:
    """Verify the credential and claim the stretch before using the model."""
    client = api.HandlerClient(bearer)
    receipt = api.Progress(client, conversation_id, turn_id, stretch_id)
    try:
        identity = client.me()
        work = receipt.take()
    except (Refused, httpx.RequestError):
        return
    try:
        execute(work, Context(receipt, identity))
    except Exception as error:
        log.warning("Copilot turn %s failed (%s)", turn_id, type(error).__name__)
        try:
            receipt.finish(
                status="failed",
                failure="The Copilot could not finish this turn. Try again.",
            )
        except (Refused, httpx.RequestError):
            log.warning("Copilot turn %s could not report its failure", turn_id)
