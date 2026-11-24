"""The claim agent tools and their runtime context and durable state."""

import json
import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, NotRequired, TypedDict

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.graph.message import add_messages
from langgraph.types import Command

from agent import api, memory
from agent.context_client import ContextClient, DocumentName, PlanName, RecordName
from agent.gateway import Gateway
from agent.screening import ScreenAttempt


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
    configuration_version: int | None = None
    memory_claim: dict[str, Any] = field(default_factory=dict)
    revision: int = 0


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
    reviews_applied: NotRequired[int]


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


ObservedAnswer = tuple[str, tuple[api.StepKind, dict[str, Any]]]


def sourced_answer(subject: str, answer: dict[str, Any]) -> ObservedAnswer:
    """Return context to the model and its lookup sources to the timeline."""
    if "source" in answer:
        sources = [answer["source"]]
    else:
        matches = {hit["source"]["file"]: hit["source"] for hit in answer["matches"]}
        sources = list(matches.values()) or answer["sources"]
    step = {"subject": subject, "sources": sources}
    return json.dumps(answer), ("lookup", step)


@tool(response_format="content_and_artifact")
def lookup_term(term: str, runtime: ToolRuntime[ClaimContext]) -> ObservedAnswer:
    """Find a policy term's definition and the sections it comes from."""
    return sourced_answer(f'Term "{term}"', lookup(runtime, "lookup_term", term=term))


@tool(response_format="content_and_artifact")
def describe_fields(
    record: RecordName, runtime: ToolRuntime[ClaimContext]
) -> ObservedAnswer:
    """Describe the fields of a claim, policy or previous claim."""
    subject = f"Fields for {record.replace('_', ' ')}"
    return sourced_answer(subject, lookup(runtime, "describe_fields", record=record))


@tool(response_format="content_and_artifact")
def get_plan(plan: PlanName, runtime: ToolRuntime[ClaimContext]) -> ObservedAnswer:
    """Read one plan's limits, waiting periods, deductible and add-ons."""
    return sourced_answer(
        f"{plan.capitalize()} plan", lookup(runtime, "get_plan", plan=plan)
    )


@tool(response_format="content_and_artifact")
def get_claim_rules(runtime: ToolRuntime[ClaimContext]) -> ObservedAnswer:
    """Read the rules claims operations applies when handling a claim."""
    return sourced_answer("Claim rules", lookup(runtime, "get_claim_rules"))


@tool(response_format="content_and_artifact")
def search_reference_data(
    query: str, runtime: ToolRuntime[ClaimContext]
) -> ObservedAnswer:
    """Find outside facts containing every word of a query."""
    return sourced_answer(
        f'Reference data search for "{query}"',
        lookup(runtime, "search_reference_data", query=query),
    )


@tool(response_format="content_and_artifact")
def search_documents(query: str, runtime: ToolRuntime[ClaimContext]) -> ObservedAnswer:
    """Find the sections of the wording and the manual containing one word or phrase."""
    return sourced_answer(
        f'Document search for "{query}"',
        lookup(runtime, "search_documents", query=query),
    )


@tool(response_format="content_and_artifact")
def read_section(
    document: DocumentName, number: int, runtime: ToolRuntime[ClaimContext]
) -> ObservedAnswer:
    """Read one section of the policy wording or the claims manual in full."""
    section = lookup(runtime, "read_section", document=document, number=number)
    read = f"{section['document_title']}, section {number}: {section['title']}"
    return sourced_answer(read, section)


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
    context = runtime.context
    return json.dumps(
        api.calculate_payout(
            context.claim_id,
            {
                "benefit": benefit,
                "lines": [line.model_dump() for line in lines],
                "paid_by_others": paid_by_others,
                "thread_id": context.thread_id,
                "revision": context.revision,
            },
            via=context.gateway,
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
    context = runtime.context
    claim = api.record_decision(
        context.claim_id,
        {
            "decision": decision,
            "payout": payout,
            "section": section,
            "reason": reason,
            "decided_by": (
                f"UnderCover Agent (configuration {context.configuration_version})"
            ),
            "thread_id": context.thread_id,
            "revision": context.revision,
        },
        via=context.gateway,
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


@tool(response_format="content_and_artifact")
def search_memory(query: str, runtime: ToolRuntime[ClaimContext]) -> ObservedAnswer:
    """Search what we remember about this claim's customer from earlier claims."""
    context = runtime.context
    if runtime.store is None:
        raise RuntimeError("The claim graph has no customer memory store")
    result = memory.search(runtime.store, context.customer_id, context.claim_id, query)
    return json.dumps(result), ("memory_search", {"query": query, "results": result})


CONTEXT_TOOLS = [
    lookup_term,
    describe_fields,
    get_plan,
    get_claim_rules,
    search_reference_data,
    search_documents,
    read_section,
]
