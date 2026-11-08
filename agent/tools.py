"""The agents' tools. The claim agent's tools, list_claims and write_todos call the
backend operations they are named after, and work_claim hands a claim to a claim agent.
"""

import json
import operator
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langchain.tools import ToolException, ToolRuntime, tool
from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.graph.message import add_messages
from langgraph.types import Command

from agent import api


@dataclass
class ClaimContext:
    """The claim a claim agent is working on."""

    claim_id: str
    run_id: str | None = None


def keep_last(_: Any, new: Any) -> Any:
    """Keep the newest value, even when two tool calls set it in the same step. With
    it, LangGraph starts the key empty, as {} or "", so the key is always there."""
    return new


class ClaimState(TypedDict):
    """What the claim graph keeps for one claim, saved after every step. The decision
    and the claim's status are as the backend recorded them, and empty until then."""

    messages: Annotated[list[AnyMessage], add_messages]
    model_calls: Annotated[int, operator.add]
    decision: Annotated[dict[str, Any], keep_last]
    status: Annotated[str, keep_last]


@dataclass
class RunContext:
    """A queue agent's run: its cap, how to work a claim, and the claims started."""

    run_id: str
    cap: int
    work_claim: Callable[[str], str]
    started: set[str] = field(default_factory=set)


@tool
def get_claim(runtime: ToolRuntime[ClaimContext]) -> str:
    """Get your claim in full: the story, the items claimed and the documents."""
    return json.dumps(api.get_claim(runtime.context.claim_id))


@tool
def get_policy(policy_id: str) -> str:
    """Get a policy: the plan, add-ons, purchase date, trip and travelers."""
    return json.dumps(api.get_policy(policy_id))


@tool
def get_claim_history(customer_id: str) -> str:
    """Get a customer's earlier claims, with the outcome and the amount paid."""
    return json.dumps(api.get_claim_history(customer_id))


@tool
def search_documents(query: str) -> str:
    """Find the sections of the wording and the manual containing one word or phrase."""
    return json.dumps(api.search_documents(query))


@tool(response_format="content_and_artifact")
def read_section(
    document: api.DocumentName, number: int
) -> tuple[str, tuple[api.StepKind, str]]:
    """Read one section of the policy wording or the claims manual in full."""
    section = api.read_section(document, number)
    read = f"{section['document_title']}, section {number}: {section['title']}"
    return json.dumps(section), ("reading", read)


@tool
def calculate_payout(
    benefit: Annotated[api.Benefit, "The benefit the claim is paid under"],
    lines: Annotated[
        list[api.Line],
        "The covered items and expenses, one for each line of the claim, with any "
        "excluded ones left out",
    ],
    paid_by_others: Annotated[
        float, "What a carrier, hotel or anyone else has paid or refunded, or 0"
    ],
    runtime: ToolRuntime[ClaimContext],
) -> str:
    """Work out the payout on your claim with UnderCover's payout rules."""
    claim_id = runtime.context.claim_id
    return json.dumps(api.calculate_payout(claim_id, benefit, lines, paid_by_others))


@tool
def record_decision(
    decision: api.DecisionKind,
    payout: Annotated[float, "In US dollars, and 0 for a decline or a referral"],
    section: Annotated[str, "The sections of the wording and the manual relied on"],
    reason: Annotated[str, "Two or three plain sentences for a colleague"],
    runtime: ToolRuntime[ClaimContext],
) -> Command:
    """Record the decision on your claim, once, which ends your work on it."""
    claim_id = runtime.context.claim_id
    claim = api.record_decision(claim_id, decision, payout, section, reason)
    message = ToolMessage(json.dumps(claim), tool_call_id=runtime.tool_call_id)
    update = {
        "messages": [message],
        "decision": claim["decision"],
        "status": claim["status"],
    }
    return Command(update=update)


@tool
def list_claims() -> str:
    """List every claim in the queue, in id order, with its status and decision."""
    return json.dumps(api.list_claims())


@tool
def write_todos(todos: list[api.Todo], runtime: ToolRuntime[RunContext]) -> str:
    """Replace the run's to-do list, sending every item with its current status."""
    return json.dumps(api.write_todos(runtime.context.run_id, todos))


@tool
def work_claim(claim_id: str, runtime: ToolRuntime[RunContext]) -> str:
    """Hand one claim to a new claim agent, which reports back in one line."""
    run = runtime.context
    if claim_id not in run.started and len(run.started) >= run.cap:
        started = ", ".join(sorted(run.started))
        raise ToolException(
            f"this run can work {run.cap} claims, and has started {started}"
        )
    report = run.work_claim(claim_id)
    run.started.add(claim_id)
    return report


CLAIM_TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    search_documents,
    read_section,
    calculate_payout,
    record_decision,
]
QUEUE_TOOLS = [list_claims, write_todos, work_claim]
