"""The claim agent tools and their runtime context and durable state."""

import json
import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.graph.message import add_messages
from langgraph.types import Command

from agent import api
from agent.context_client import ContextClient
from agent.gateway import Gateway
from agent.screening import ScreenAttempt
from context.catalog import DocumentName, PlanName, RecordName


@dataclass
class ClaimContext:
    """The claim a claim agent is working on."""

    claim_id: str
    thread_id: str
    run_id: str | None = None
    signed_off_status: str | None = None
    evaluation_id: str | None = None
    policy_id: str = ""
    customer_id: str = ""
    gateway: Gateway | None = None
    knowledge: ContextClient | None = None


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
    screening: ScreenAttempt
    screen_attempts: Annotated[int, operator.add]


@tool
def get_claim(runtime: ToolRuntime[ClaimContext]) -> str:
    """Get your claim in full: the story, the items claimed and the documents."""
    return api.claim_text(runtime.context.claim_id, via=runtime.context.gateway)


@tool
def get_policy(runtime: ToolRuntime[ClaimContext]) -> str:
    """Get your claim's policy: its plan, add-ons, purchase date, trip and travelers."""
    context = runtime.context
    return json.dumps(api.get_policy(context.policy_id, via=context.gateway))


@tool
def get_claim_history(runtime: ToolRuntime[ClaimContext]) -> str:
    """Get this customer's earlier claims, with the outcomes and amounts paid."""
    context = runtime.context
    return json.dumps(api.get_claim_history(context.customer_id, via=context.gateway))


def lookup(
    runtime: ToolRuntime[ClaimContext], operation: str, **arguments: Any
) -> dict:
    """Call the route selected for this graph's context tools."""
    knowledge = runtime.context.knowledge
    if knowledge is None:
        raise RuntimeError("The claim graph has no context client")
    return knowledge.call(operation, **arguments)


@tool
def lookup_term(term: str, runtime: ToolRuntime[ClaimContext]) -> str:
    """Find a policy term's definition and the sections it comes from."""
    return json.dumps(lookup(runtime, "lookup_term", term=term))


@tool
def describe_fields(record: RecordName, runtime: ToolRuntime[ClaimContext]) -> str:
    """Describe the fields of a claim, policy or previous claim."""
    return json.dumps(lookup(runtime, "describe_fields", record=record))


@tool
def get_plan(plan: PlanName, runtime: ToolRuntime[ClaimContext]) -> str:
    """Read one plan's limits, waiting periods, deductible and add-ons."""
    return json.dumps(lookup(runtime, "get_plan", plan=plan))


@tool
def get_claim_rules(runtime: ToolRuntime[ClaimContext]) -> str:
    """Read the rules claims operations applies when handling a claim."""
    return json.dumps(lookup(runtime, "get_claim_rules"))


@tool
def search_reference_data(query: str, runtime: ToolRuntime[ClaimContext]) -> str:
    """Find outside facts containing every word of a query."""
    return json.dumps(lookup(runtime, "search_reference_data", query=query))


@tool
def search_documents(query: str, runtime: ToolRuntime[ClaimContext]) -> str:
    """Find the sections of the wording and the manual containing one word or phrase."""
    return json.dumps(lookup(runtime, "search_documents", query=query))


@tool(response_format="content_and_artifact")
def read_section(
    document: DocumentName, number: int, runtime: ToolRuntime[ClaimContext]
) -> tuple[str, tuple[api.StepKind, str]]:
    """Read one section of the policy wording or the claims manual in full."""
    section = lookup(runtime, "read_section", document=document, number=number)
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
    return json.dumps(
        api.calculate_payout(
            claim_id, benefit, lines, paid_by_others, via=runtime.context.gateway
        )
    )


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
    claim = api.record_decision(
        claim_id,
        {"decision": decision, "payout": payout, "section": section, "reason": reason},
        via=runtime.context.gateway,
    )
    message = ToolMessage(json.dumps(claim), tool_call_id=runtime.tool_call_id)
    update = {
        "messages": [message],
        "decision": claim["decision"],
        "status": claim["status"],
    }
    return Command(update=update)


CLAIM_TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    calculate_payout,
    record_decision,
]

CONTEXT_TOOLS = [
    lookup_term,
    describe_fields,
    get_plan,
    get_claim_rules,
    search_reference_data,
    search_documents,
    read_section,
]
