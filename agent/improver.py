"""The sixth agent part: durable, human-approved configuration improvement."""

import contextvars
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
)
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent import checkpoints, improver_api, measurements, models, run_execution

ROUND_MODEL_CALLS = 30
INSTRUCTIONS = """Your goal is an accurate, well-grounded and efficient claims agent,
within the business objectives, comparison rule and spending limit in your results.
The claim agent works from its instructions plus the policy wording and claims manual,
the claim, its policy and the customer's earlier claims. Its tools search and read
sections, calculate payouts, record decisions and pay approved claims.
Your changes generalize to claims and never name an individual claim. Only the allowed
models and efforts are available. Handler approvals authorize testing; only the
backend's held-out rule makes a configuration current. Rejection feedback is evidence
for a better proposal. Held-out examples and labels are unavailable to you.
Your recipe is versioned data for this round. Your goal includes a useful recipe for
future rounds, subject to handler approval. Your four tools support this recipe:

{recipe}"""


@dataclass
class RoundContext:
    round_id: str


class RoundUsage(AgentMiddleware[Any, RoundContext]):
    """Check admission and record the provider's actual charge for each reply."""

    def wrap_model_call(
        self,
        request: ModelRequest[RoundContext],
        handler: Callable[[ModelRequest[RoundContext]], ModelResponse],
    ) -> ModelResponse:
        identity = request.runtime.context.round_id
        improver_api.admit(identity)
        response = handler(request)
        for reply in response.result:
            if isinstance(reply, AIMessage):
                improver_api.usage(identity, measurements.model_reply(reply, None))
        return response


def proposal_request(name: str, arguments: dict, identity: str) -> dict:
    """Keep the checkpoint's tool-call id as the durable proposal identity."""
    return {
        "proposal_id": identity,
        "kind": "configuration" if name == "propose_change" else "recipe",
        "arguments": arguments,
    }


def needs_approval(request: ToolCallRequest) -> bool:
    """Let invalid proposals return a tool refusal without pausing for a handler."""
    call = request.tool_call
    proposal = proposal_request(call["name"], call["args"], cast(str, call["id"]))
    return (
        improver_api.validate_proposal(
            cast(RoundContext, request.runtime.context).round_id, proposal
        )
        is None
    )


@tool
def read_results(runtime: ToolRuntime[RoundContext]) -> str:
    """Read development examples, held-out aggregate scores and remaining budget."""
    return json.dumps(improver_api.read_results(runtime.context.round_id))


def approved_proposal(
    runtime: ToolRuntime[RoundContext], name: str, arguments: dict
) -> str:
    """Return the saved approval result, or a model-facing validation refusal."""
    identity = runtime.context.round_id
    request = proposal_request(name, arguments, cast(str, runtime.tool_call_id))
    refusal = improver_api.validate_proposal(identity, request)
    round_record = improver_api.get_round(identity)
    proposal = next(
        (
            p
            for p in round_record["proposals"]
            if p["proposal_id"] == runtime.tool_call_id
        ),
        None,
    )
    if proposal and proposal["decision"] == "approve":
        return json.dumps(
            {"approved_version": proposal["version"], "kind": proposal["kind"]}
        )
    return refusal or "This proposal has not been approved"


@tool
def propose_change(
    instructions: str,
    model: str,
    reasoning_effort: str,
    reason: str,
    runtime: ToolRuntime[RoundContext],
) -> str:
    """Propose complete instructions, an allowed model and effort, and a reason."""
    return approved_proposal(
        runtime,
        "propose_change",
        {
            "instructions": instructions,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "reason": reason,
        },
    )


def evaluate(round_id: str, version: int, set_name: str) -> dict:
    """Run an affordable whole-set evaluation within the already-held work slot."""
    quote = improver_api.quote(round_id, version, set_name)
    if not quote["allowed"]:
        return quote
    current = improver_api.get_round(round_id)
    result = contextvars.Context().run(
        run_execution.execute,
        {
            "kind": "evaluation",
            "configuration_version": version,
            "round_id": round_id,
            "concurrency": current["settings"]["concurrency"],
            "set_name": set_name,
            "tiny": False,
        },
    )
    return {"allowed": True, "status": result["status"], "run_id": result["run_id"]}


@tool
def run_evaluation(runtime: ToolRuntime[RoundContext]) -> str:
    """Run the candidate's three prescribed evaluations and obtain the verdict."""
    identity = runtime.context.round_id
    current = improver_api.get_round(identity)
    version = current["candidate_version"]
    if version is None:
        return "A handler must approve a configuration before testing it"
    if current["verdict"] is not None:
        return json.dumps(
            {"verdict": current["verdict"], "reason": current["explanation"]}
        )
    quote = improver_api.sequence_quote(identity)
    if not quote["allowed"]:
        return json.dumps({"refusal": quote, "round": improver_api.verdict(identity)})
    for index, name in enumerate(("evaluation", "held_out", "held_out"), 1):
        improver_api.progress(identity, "evaluating", index)
        result = evaluate(identity, version, name)
        if not result["allowed"] or result.get("status") != "finished":
            break
    return json.dumps(improver_api.verdict(identity))


@tool
def propose_recipe(recipe: str, reason: str, runtime: ToolRuntime[RoundContext]) -> str:
    """Propose the complete recipe for future rounds and explain the change."""
    return approved_proposal(
        runtime, "propose_recipe", {"recipe": recipe, "reason": reason}
    )


IMPROVER_TOOLS = [read_results, propose_change, run_evaluation, propose_recipe]


def build_graph(
    current: dict,
    recipe: str,
    saver: BaseCheckpointSaver,
    model: BaseChatModel | None = None,
):
    """Build the four-tool agent with durable limits and proposal interruptions."""
    llm = (
        model
        if model is not None
        else models.build_model(
            {
                "model": current["improver_model"],
                "reasoning_effort": current["improver_effort"],
            },
            parallel_tool_calls=False,
        )
    )
    return create_agent(
        llm,
        tools=IMPROVER_TOOLS,
        system_prompt=INSTRUCTIONS.format(recipe=recipe),
        context_schema=RoundContext,
        checkpointer=saver,
        middleware=cast(
            list[AgentMiddleware[Any, RoundContext]],
            [
                ModelCallLimitMiddleware(
                    thread_limit=ROUND_MODEL_CALLS, exit_behavior="end"
                ),
                RoundUsage(),
                HumanInTheLoopMiddleware(
                    interrupt_on={
                        name: {
                            "allowed_decisions": ["approve", "reject"],
                            "when": needs_approval,
                        }
                        for name in ("propose_change", "propose_recipe")
                    }
                ),
            ],
        ),
    )


def prepare_baseline(round_id: str) -> bool:
    """Work only missing baseline sets, stopping if their budget cannot cover them."""
    missing = improver_api.baseline(round_id)
    for index, name in enumerate(missing["missing"], 4 - len(missing["missing"])):
        improver_api.progress(round_id, "baseline", index)
        result = evaluate(round_id, missing["configuration_version"], name)
        if not result["allowed"] or result.get("status") != "finished":
            improver_api.finish(
                round_id, True, result.get("reason", "Baseline evaluation stopped")
            )
            return False
    improver_api.baseline(round_id)
    return True


def round_config(current: dict) -> dict:
    """Keep one checkpoint thread and Langfuse session across approval stretches."""
    round_id = current["round_id"]
    return {
        "configurable": {"thread_id": current["thread_id"]},
        "callbacks": [CallbackHandler()],
        "metadata": {
            "langfuse_session_id": round_id,
            "langfuse_trace_name": "Improve configuration",
            "langfuse_tags": [round_id],
        },
        "recursion_limit": 200,
        "max_concurrency": 1,
    }


def finalize_stretch(
    round_id: str, graph: Any, config: dict, interrupted: bool
) -> None:
    """Publish a saved approval pause or finalize the round's backend verdict."""
    if interrupted:
        state = graph.get_state(config)
        calls = state.values["messages"][-1].tool_calls
        for call in calls:
            if call["name"] in ("propose_change", "propose_recipe"):
                improver_api.pending(
                    round_id, proposal_request(call["name"], call["args"], call["id"])
                )
                return
    current = improver_api.get_round(round_id)
    if current["candidate_version"] is not None and current["verdict"] is None:
        improver_api.verdict(round_id)
    improver_api.finish(
        round_id,
        False,
        "The improver finished without proposing a configuration"
        if current["candidate_version"] is None
        else "The improver completed this round",
    )


def work(round_id: str, decision: dict | None = None, graph: Any = None) -> None:
    """Run one stretch until a durable approval pause or the round's completion."""
    current = improver_api.get_round(round_id)
    if decision is None and not prepare_baseline(round_id):
        return
    if graph is None:
        recipe = improver_api.read_results(round_id)["recipe"]["recipe"]
        graph = build_graph(current, recipe, checkpoints.new_saver())
    config = round_config(current)
    start = (
        Command(resume={"decisions": [decision]})
        if decision
        else {
            "messages": [
                {
                    "role": "user",
                    "content": "Improve within the objectives and budget.",
                }
            ]
        }
    )
    improver_api.progress(round_id, "working", 0)
    result = contextvars.Context().run(
        graph.invoke, start, config, context=RoundContext(round_id), durability="sync"
    )
    finalize_stretch(round_id, graph, config, bool(result.get("__interrupt__")))
