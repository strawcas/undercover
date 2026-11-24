"""Build and execute the durable claim graph, with one saver per execution."""

import contextvars
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import replace
from functools import cache, partial
from http import HTTPStatus
from threading import Condition
from time import monotonic
from typing import Any, cast

import httpx
from langchain.tools import ToolException
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable, RunnableConfig
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command, Overwrite, interrupt

from agent import (
    api,
    checkpoints,
    context_client,
    gateway,
    measurements,
    memory,
    models,
    observations,
    screening,
)
from agent.backend_client import Refused
from agent.heartbeat import heartbeat
from agent.tools import (
    CLAIM_TOOLS,
    CONTEXT_TOOLS,
    ClaimContext,
    ClaimState,
    search_memory,
)

log = logging.getLogger(__name__)

ClaimGraph = CompiledStateGraph[ClaimState, ClaimContext, Any, Any]
Messages = dict[str, list[BaseMessage]]
Start = Messages | Command | None

CLAIM_MODEL_CALLS = 30
NO_DECISION = "The agent finished without recording a decision"
REVIEW_GUIDANCE = (
    "On a returned claim, senior review takes precedence over general guidance "
    "about final decisions. Treat the note as feedback on the previous decision. "
    "Check it against the policy wording and claims manual. Refer the claim to a "
    "handler and explain why if the wording does not support the requested change."
)
DECISION_REVISION = "undercover_decision_revision"
WORK_CLAIM = "Work claim"
CARRY_ON = "Carry on"
SCREEN = "screen_claim"
RECORD_SCREEN = "record_screen"
AGENT = "agent"
TOOLS = "tools"
PAUSE = "wait_for_sign_off"
STAGE_REVIEW = "stage_review"
PAY = "pay"
SAVE_MEMORY = "save_memory"


claim_lock = Condition()
owned_claims: set[str] = set()
pending_resumes: set[str] = set()


def reserve_claim(claim_id: str) -> bool:
    """Reserve a claim before work is scheduled, including sign-off resumes."""
    with claim_lock:
        if claim_id in owned_claims or claim_id in pending_resumes:
            return False
        owned_claims.add(claim_id)
        return True


def release_claim(claim_id: str) -> None:
    """Release ownership after completion, pause or failure."""
    with claim_lock:
        owned_claims.discard(claim_id)
        claim_lock.notify_all()


def is_reserved(claim_id: str) -> bool:
    """Check whether another execution owns the claim."""
    with claim_lock:
        return claim_id in owned_claims or claim_id in pending_resumes


def queue_resume(claim: dict[str, Any]) -> bool:
    """Reserve a sign-off handoff even while the worker is reaching its pause."""
    claim_id = claim["claim_id"]
    with claim_lock:
        if claim_id in pending_resumes:
            return False
        if claim_id not in owned_claims and not paused(claim):
            raise Refused(409, f"Claim {claim_id} has no pending sign-off checkpoint")
        pending_resumes.add(claim_id)
        return True


def take_resume(claim_id: str) -> None:
    """Wait for the current worker, then atomically take its reserved handoff."""
    with claim_lock:
        claim_lock.wait_for(lambda: claim_id not in owned_claims)
        pending_resumes.remove(claim_id)
        owned_claims.add(claim_id)


def cancel_resume(claim_id: str) -> None:
    """Remove a queued handoff that the backend did not award."""
    with claim_lock:
        pending_resumes.discard(claim_id)


def execution_graph(
    version: int | None = None, *, evaluation: bool = False
) -> ClaimGraph:
    """Give each execution its own saver on the initialized process pool."""
    configuration = (
        api.get_current_configuration()
        if version is None
        else api.get_configuration(version)
    )
    return claim_graph(
        models.build_model(configuration),
        checkpoints.new_saver(),
        configuration,
        evaluation=evaluation,
    )


def execute_reserved_claim(claim_id: str, run_id: str | None, version: int) -> str:
    """Start or resume an owned claim, retrying ordinary failed attempts fresh."""
    try:
        claim = api.get_claim(claim_id)
        if claim["returned_decisions"]:
            owner = str(uuid.uuid4())
            try:
                claim = api.take_review(claim_id, owner, run_id)
            except Refused as error:
                if error.status_code != HTTPStatus.CONFLICT:
                    raise
                return f"Skipped: {error.detail}"
            return work_returned(claim, owner, run_id, version)
        return execute_attempts(claim, run_id, version)
    finally:
        release_claim(claim_id)


def execute_attempts(
    claim: dict[str, Any], run_id: str | None, version: int | None
) -> str:
    """Retry an ordinary failed attempt while this decision revision is current."""
    claim_id, revision = claim["claim_id"], claim["revision"]
    while True:
        api.admit_claim(claim_id, run_id, revision)
        if claim["status"] == api.Status.WAITING:
            line = work_claim(claim_id, None, run_id, version, revision=revision)
        elif claim["status"] == api.Status.IN_PROGRESS:
            line = carry_on(claim, None, run_id, version)
        else:
            return "No eligible work"
        claim = api.get_claim(claim_id)
        if claim["status"] != api.Status.WAITING or claim["revision"] != revision:
            return line


def work_returned(
    claim: dict[str, Any], owner: str, run_id: str | None, version: int | None
) -> str:
    """Keep the worker's review lease through the entire returned claim."""
    claim_id = claim["claim_id"]
    try:
        with heartbeat(partial(api.review_heartbeat, claim_id, owner, working=True)):
            return execute_attempts(claim, run_id, version)
    finally:
        release_review(claim_id, owner)


def release_review(claim_id: str, owner: str) -> None:
    """Release the current worker's lease without masking the original result."""
    try:
        api.review_heartbeat(claim_id, owner, working=False)
    except Refused as error:
        if error.status_code != HTTPStatus.CONFLICT:
            log.warning("%s could not release review work: %s", claim_id, error)
    except httpx.RequestError as error:
        log.warning("%s could not release review work: %s", claim_id, error)


def execute_claim(claim_id: str, run_id: str, version: int) -> str:
    """The dispatcher's single boundary to the claim agent."""
    if not reserve_claim(claim_id):
        return "Skipped: claim is already being resumed"
    return execute_reserved_claim(claim_id, run_id, version)


class TooManyModelCalls(Exception):
    """The claim agent made as many model calls as it may without finishing."""


def refusal(error: Exception) -> str:
    """Show the model why a tool was refused, and let any other error stop the agent."""
    if isinstance(error, Refused):
        detail = error.detail
        if isinstance(detail, list):
            detail = " ".join(
                item["msg"].removeprefix("Value error, ") for item in detail
            )
        return f"{observations.APP_REFUSAL}{error.status_code}: {detail}"
    if isinstance(error, gateway.GatewayDenied):
        return f"{observations.GATEWAY_REFUSAL}{error}"
    if isinstance(error, ToolException):
        return f"{observations.TOOL_REFUSAL}{error}"
    raise error


def record_result(
    request: ToolCallRequest,
    execute: Callable[[ToolCallRequest], ToolMessage | Command],
    *,
    via: gateway.Gateway | None,
    knowledge: context_client.ContextClient,
    configuration_version: Callable[[], int],
) -> ToolMessage | Command:
    """Run a tool and record its result on the claim, then any step it gives as its
    artifact, such as a section it read."""
    context = cast(ClaimContext, request.runtime.context)
    reply = request.state["messages"][-1]
    if reply.response_metadata.get(DECISION_REVISION, 0) != context.revision:
        return ToolMessage(
            "This work was superseded by the senior review.",
            tool_call_id=cast(str, request.tool_call["id"]),
        )
    runtime = replace(
        request.runtime,
        context=replace(
            context,
            gateway=via,
            knowledge=knowledge,
            configuration_version=configuration_version(),
        ),
    )
    result = execute(replace(request, runtime=runtime))
    if isinstance(result, ToolMessage):
        message = result
    else:
        message = cast(dict[str, Any], result.update)["messages"][0]
    observations.record_tool_result(context, request.tool_call["name"], message, via)
    return result


def call_model(
    state: ClaimState,
    runtime: Runtime[ClaimContext],
    llm: Runnable[LanguageModelInput, BaseMessage],
    instructions: Callable[[], SystemMessage],
    via: gateway.Gateway | None,
) -> Command[str]:
    """Ask the model for its next step, up to CLAIM_MODEL_CALLS times in all, and
    record its reply on the claim."""
    calls = state["model_calls"]
    attempt = state.get("screening")
    if not calls:
        if not attempt or attempt["result"] is None:
            return Command(goto=SCREEN)
        if attempt["result"]["instructions_found"]:
            return Command(goto=RECORD_SCREEN)
    if calls >= CLAIM_MODEL_CALLS:
        message = f"The claim agent made {calls} model calls without finishing"
        raise TooManyModelCalls(message)
    context = runtime.context
    api.admit_claim(context.claim_id, context.run_id, context.revision)
    prompt = instructions()
    if state.get("reviews_applied"):
        prompt = SystemMessage(f"{prompt.text}\n\n{REVIEW_GUIDANCE}")
    reply = llm.invoke([prompt, *state["messages"]])
    api.record_model_call(
        runtime.context.claim_id,
        measurements.model_reply(reply, runtime.context.run_id),
    )
    if context.revision:
        api.admit_claim(context.claim_id, context.run_id, context.revision)
        reply.response_metadata[DECISION_REVISION] = context.revision
    observations.record_reply(context, reply, via)
    return Command(
        update={"messages": [reply], "model_calls": 1},
        goto=TOOLS if isinstance(reply, AIMessage) and reply.tool_calls else END,
    )


def wait_for_sign_off(
    state: ClaimState, runtime: Runtime[ClaimContext]
) -> dict[str, Any]:
    """Wait on the saved claim until a senior handler answers."""
    answer = interrupt({"claim_id": runtime.context.claim_id})
    if answer is True:
        return {"status": runtime.context.signed_off_status}
    return {
        "status": api.Status.IN_PROGRESS,
        "decision": {},
        "model_calls": Overwrite(0),
        "reviews_applied": answer["number"],
        "messages": [review_message(answer)],
    }


def review_message(review: dict[str, Any]) -> HumanMessage:
    """Give the handler's attributed feedback with the graph's review rules."""
    return HumanMessage(
        f"Senior handler {review['handler']} sent the approval back with this note: "
        f"{json.dumps(review['note'], ensure_ascii=False)}. {REVIEW_GUIDANCE}"
    )


def screen_claim(state: ClaimState, runtime: Runtime[ClaimContext]) -> dict[str, Any]:
    """Save the screening result before logging it or recording a referral."""
    context = runtime.context
    attempt = screening.check(context.claim_id, context.run_id, context.revision)
    if context.evaluation_id is not None and attempt["usage"] is not None:
        # Startup discards stopped evaluation checkpoints, so save the charge first.
        api.record_model_call(context.claim_id, attempt["usage"])
        attempt["usage"] = None
    return {
        "screening": attempt,
        "screen_attempts": 1,
    }


def record_screen(state: ClaimState, runtime: Runtime[ClaimContext]) -> dict[str, Any]:
    """Record the saved result once and refer any flagged claim."""
    context, attempt = runtime.context, state["screening"]
    if attempt["usage"] is not None:
        api.record_model_call(context.claim_id, attempt["usage"])
    result = attempt["result"]
    if result is None:
        if state["screen_attempts"] >= screening.MAX_ATTEMPTS:
            raise RuntimeError(
                f"{screening.SECTION} could not read the model response "
                f"after {screening.MAX_ATTEMPTS} tries"
            )
        return {}
    flagged, reason = result["instructions_found"], result["reason"]
    label = "Flagged" if flagged else "Passed"
    api.record_step(
        context.claim_id, "security", f"{label}. {reason}", thread_id=context.thread_id
    )
    if flagged:
        claim = api.record_decision(
            context.claim_id,
            {
                "decision": "refer",
                "payout": 0,
                "section": screening.SECTION,
                "reason": reason,
                "decided_by": screening.SECTION,
                "thread_id": context.thread_id,
                "revision": context.revision,
            },
        )
        return {"decision": claim["decision"], "status": claim["status"]}
    return {}


def after_screen(state: ClaimState) -> str:
    """Send only cleared claims to the adjudication model."""
    result = state["screening"]["result"]
    if result is None:
        return SCREEN
    return END if result["instructions_found"] else AGENT


def pay(
    state: ClaimState, runtime: Runtime[ClaimContext], *, via: gateway.Gateway | None
) -> dict[str, Any]:
    """Preserve a recorded sign-off outcome or request an ordinary payout."""
    if runtime.context.signed_off_status is not None:
        return {"status": runtime.context.signed_off_status}
    context = runtime.context
    amount = state["decision"]["payout"]
    try:
        observations.record_call(context, "pay_claim", {"amount": amount}, via)
    except Exception as error:
        log.warning("%s could not record the payment call: %s", context.claim_id, error)
    try:
        claim = api.pay_claim(
            context.claim_id, amount, via=via, revision=context.revision
        )
    except gateway.GatewayDenied as denied:
        claim = api.hold_gateway_payment(
            context.claim_id, str(denied), context.revision
        )
    except Refused as denied:
        if denied.status_code != HTTPStatus.FORBIDDEN:
            observations.record_refusal(context, "pay_claim", refusal(denied))
            raise
        claim = api.hold_limit_payment(context.claim_id, context.revision)
        observations.record_refusal(context, "pay_claim", refusal(denied))
    return {"status": claim["status"]}


def after_payment(state: ClaimState, runtime: Runtime[ClaimContext]) -> str:
    """Pause an original when the Gateway held its payout."""
    if state["status"] == api.Status.WAITING_FOR_SIGN_OFF:
        return PAUSE if runtime.context.evaluation_id is None else END
    return SAVE_MEMORY if runtime.store is not None else END


def after_review(state: ClaimState, runtime: Runtime[ClaimContext]) -> str:
    """Start a replacement decision or finish a paid sign-off."""
    return (
        AGENT
        if state["status"] == api.Status.IN_PROGRESS
        else after_payment(state, runtime)
    )


def after_tools(state: ClaimState, runtime: Runtime[ClaimContext]) -> str:
    """Continue adjudication or settle a recorded decision before saving memory."""
    if not state["decision"]:
        return AGENT
    if state["status"] == api.Status.WAITING_FOR_SIGN_OFF:
        return after_payment(state, runtime)
    if (
        state["decision"]["decision"] == api.APPROVE
        and state["status"] == api.Status.IN_PROGRESS
    ):
        return PAY
    return SAVE_MEMORY if runtime.store is not None else END


def save_memory(state: ClaimState, runtime: Runtime[ClaimContext]) -> dict[str, Any]:
    """Save the recorded decision and minimal claim after adjudication."""
    if runtime.store is not None and state["status"] == api.Status.DECIDED:
        context = runtime.context
        api.prepare_memory(context.claim_id, context.thread_id, context.revision)
        memory.save_decision(runtime.store, context.memory_claim, state["decision"])
    return {}


def after_memory(state: ClaimState, runtime: Runtime[ClaimContext]) -> str:
    """Settle approvals from older checkpoints that reached memory first."""
    if state["status"] == api.Status.WAITING_FOR_SIGN_OFF:
        return PAUSE if runtime.context.evaluation_id is None else END
    return PAY if state["status"] == api.Status.IN_PROGRESS else END


def claim_graph(
    llm: BaseChatModel,
    saver: BaseCheckpointSaver[Any],
    configuration: dict[str, Any] | None = None,
    *,
    evaluation: bool = False,
) -> ClaimGraph:
    """Build the claim agent's graph, which saves its progress after every step."""

    @cache
    def pinned_configuration() -> dict[str, Any]:
        return configuration or api.get_current_configuration()

    def instructions() -> SystemMessage:
        return SystemMessage(pinned_configuration()["prompt"])

    via = gateway.configured()
    knowledge = context_client.configured(via)
    tools = CLAIM_TOOLS + CONTEXT_TOOLS
    store = memory.configured(evaluation=evaluation)
    if store is not None:
        tools.append(search_memory)
    builder = StateGraph(ClaimState, context_schema=ClaimContext)
    builder.add_node(SCREEN, screen_claim)
    builder.add_node(RECORD_SCREEN, record_screen)
    builder.add_node(
        AGENT,
        partial(
            call_model, llm=llm.bind_tools(tools), instructions=instructions, via=via
        ),
        destinations=(SCREEN, RECORD_SCREEN, TOOLS, END),
    )
    builder.add_node(
        TOOLS,
        ToolNode(
            tools,
            handle_tool_errors=refusal,
            wrap_tool_call=partial(
                record_result,
                via=via,
                knowledge=knowledge,
                configuration_version=lambda: pinned_configuration()["version"],
            ),
        ),
    )
    builder.add_node(PAUSE, wait_for_sign_off)
    builder.add_node(STAGE_REVIEW, lambda state: {})
    builder.add_node(PAY, partial(pay, via=via))
    builder.add_node(SAVE_MEMORY, save_memory)
    builder.add_edge(START, SCREEN)
    builder.add_edge(SCREEN, RECORD_SCREEN)
    builder.add_conditional_edges(RECORD_SCREEN, after_screen, [SCREEN, AGENT, END])
    builder.add_conditional_edges(
        TOOLS, after_tools, [AGENT, SAVE_MEMORY, PAUSE, PAY, END]
    )
    builder.add_conditional_edges(SAVE_MEMORY, after_memory, [PAY, PAUSE, END])
    builder.add_edge(STAGE_REVIEW, PAUSE)
    builder.add_conditional_edges(PAUSE, after_review, [AGENT, SAVE_MEMORY, END])
    builder.add_conditional_edges(PAY, after_payment, [PAUSE, SAVE_MEMORY, END])
    return builder.compile(checkpointer=saver, store=store)


def thread_of(claim: dict[str, Any]) -> RunnableConfig:
    """Address the claim's thread, where the claim graph saves its progress."""
    return {"configurable": {"thread_id": claim["thread_id"]}}


def claim_config(
    claim: dict[str, Any], name: str, run_id: str | None = None
) -> RunnableConfig:
    """Run on the claim's thread, traced in Langfuse in the session of that thread."""
    session = f"{claim['claim_id']}:{claim['thread_id']}"
    run = api.get_run(run_id) if run_id else None
    tags = [run_id] if run_id else []
    if run and run.get("round_id"):
        tags.append(run["round_id"])
    return thread_of(claim) | {
        "callbacks": [CallbackHandler()],
        "metadata": {
            "langfuse_session_id": session,
            "langfuse_trace_name": name,
            "langfuse_tags": tags,
        },
        # One tool call at a time, so two decisions in one reply can't race.
        "max_concurrency": 1,
    }


def ask(text: str) -> Messages:
    """Give an agent one message from the user."""
    return {"messages": [HumanMessage(text)]}


def begin(claim_id: str) -> Messages:
    """Give a claim agent its first message, naming the claim it works on."""
    return ask(f"Claim {claim_id}")


def review_for(claim: dict[str, Any]) -> dict[str, Any] | None:
    """Use the latest recorded review and its ordinal on the claim."""
    reviews = claim["returned_decisions"]
    if not reviews:
        return None
    latest = reviews[-1]
    return {
        "number": len(reviews),
        "note": latest["note"],
        "handler": latest["handler"],
    }


def begin_attempt(claim: dict[str, Any]) -> Messages | dict[str, Any]:
    """Keep the latest review if ordinary retries have to screen a fresh thread."""
    start = begin(claim["claim_id"])
    if review := review_for(claim):
        return {
            "messages": [*start["messages"], review_message(review)],
            "reviews_applied": review["number"],
        }
    return start


def describe(error: Exception) -> str:
    """Say in one line what went wrong."""
    lines = str(error).splitlines()
    return f"{type(error).__name__}: {lines[0]}" if lines else type(error).__name__


def report(claim: dict[str, Any], reason: str) -> str:
    """Say in one line how a claim ended: its decision and payout, or why it failed."""
    claim_id, status, decision = claim["claim_id"], claim["status"], claim["decision"]
    if status == api.Status.WAITING:
        return f"{claim_id}  failed: {reason}, back in the queue"
    if status == api.Status.LEFT_FOR_HANDLER:
        return f"{claim_id}  failed: {reason}, left for a handler"
    if status == api.Status.SENT_BACK:
        return f"{claim_id}  returned for review"
    line = f"{claim_id}  {decision['decision']}  ${decision['payout']:,.2f}"
    if status == api.Status.WAITING_FOR_SIGN_OFF:
        return f"{line}, waiting for sign-off"
    if decision["decision"] == api.APPROVE and claim["payment"] is None:
        return f"{line}, not paid yet"
    return line


def run_claim_graph(
    graph: ClaimGraph | None,
    claim: dict[str, Any],
    name: str,
    run_id: str | None = None,
    version: int | None = None,
) -> str | None:
    """Run the claim graph on the claim's thread, saving each step, and say what went
    wrong, if anything."""
    slice_id, started = str(uuid.uuid4()), monotonic()

    def progress() -> None:
        api.record_work_slice(
            claim["claim_id"], slice_id, run_id, monotonic() - started
        )

    error = None
    signed_off = claim["status"] if paid_on_sign_off(claim) else None
    try:
        if signed_off is None:
            progress()
        graph = graph or execution_graph(
            version, evaluation=claim["evaluation_id"] is not None
        )
        start = (
            begin_attempt(claim) if name == WORK_CLAIM else resume_input(graph, claim)
        )

        # A fresh context gives every claim its own root trace,
        # which Langfuse needs to put it in the claim's session.
        def execute(ready: ClaimGraph) -> None:
            for update in ready.stream(
                start,
                claim_config(claim, name, run_id),
                context=ClaimContext(
                    claim_id=claim["claim_id"],
                    thread_id=claim["thread_id"],
                    run_id=run_id,
                    signed_off_status=signed_off,
                    evaluation_id=claim["evaluation_id"],
                    policy_id=claim["policy_id"],
                    customer_id=claim["customer_id"],
                    memory_claim=memory.claim_fields(claim),
                    revision=claim["revision"],
                ),
                durability="sync",
                stream_mode="updates",
            ):
                if (signed_off is None and SCREEN not in update) or PAY in update:
                    progress()

        contextvars.Context().run(execute, graph)
        if answer := newly_paused(graph, claim, start):
            start = Command(resume=answer)
            contextvars.Context().run(execute, graph)
    except (ModelRateLimitError, ModelTimeoutError):
        raise
    except Exception as problem:  # a claim that fails must not stop the run
        error = describe(problem)
    finally:
        try:
            progress()
        except Exception as problem:
            error = error or describe(problem)
    return error


def settle(claim_id: str, error: str | None, revision: int = 0) -> str:
    """Fail a claim the agent left undecided, and report how the claim ended. An
    error after the decision is logged, and the claim keeps its decision."""
    claim = api.get_claim(claim_id)
    reason = error or NO_DECISION
    if claim["revision"] != revision:
        return f"{claim_id}  handed off for review"
    if claim["status"] == api.Status.IN_PROGRESS and claim["decision"] is None:
        claim = api.fail_claim(claim_id, reason, revision)
    elif error:
        log.warning("%s  %s", claim_id, error)
    line = report(claim, reason)
    log.info(line)
    return line


def resume_input(graph: ClaimGraph, claim: dict[str, Any]) -> Start:
    """Resume a paused checkpoint or restart a thread with no saved first step."""
    saved = graph.get_state(thread_of(claim))
    answer = resume_answer(claim, saved.values)
    if saved.interrupts and answer:
        return Command(resume=answer)
    if saved.created_at is None and claim["decision"] is None:
        return begin_attempt(claim)
    if isinstance(answer, dict) and saved.next != (PAUSE,):
        # A review can commit before the decision's preceding graph step is saved.
        graph.update_state(
            thread_of(claim), review_checkpoint(saved.values), as_node=STAGE_REVIEW
        )
    return None


def review_checkpoint(state: dict[str, Any]) -> dict[str, Any]:
    """Close pending tool calls from the superseded decision before resuming."""
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage):
        return {}
    return {
        "messages": [
            ToolMessage(
                "This work was superseded by the senior review.",
                tool_call_id=call["id"],
            )
            for call in last.tool_calls
            if call.get("id") is not None
        ]
    }


def resume_answer(claim: dict[str, Any], state: dict[str, Any]) -> bool | dict | None:
    """Resume with the paid sign-off or a review the graph has not consumed."""
    if paid_on_sign_off(claim):
        return True
    review = review_for(claim)
    if (
        review
        and claim["status"] == api.Status.IN_PROGRESS
        and review["number"] > state.get("reviews_applied", 0)
    ):
        return review
    return None


def newly_paused(
    graph: ClaimGraph, claim: dict[str, Any], start: Start
) -> bool | dict | None:
    """Find a review whose worker first had to reach the actual pause."""
    if start is not None or not (
        paid_on_sign_off(claim) or claim["returned_decisions"]
    ):
        return None
    saved = graph.get_state(thread_of(claim))
    return resume_answer(claim, saved.values) if saved.interrupts else None


def paused(claim: dict[str, Any]) -> bool:
    """Check for an actual pending interrupt on the existing claim thread."""
    return bool(execution_graph().get_state(thread_of(claim)).interrupts)


def paid_on_sign_off(claim: dict[str, Any]) -> bool:
    """Use the recorded payment to recognize a completed senior settlement."""
    return bool(claim["payment"] and claim["payment"]["signed_off_by"])


def work_claim(
    claim_id: str,
    graph: ClaimGraph | None,
    run_id: str | None = None,
    version: int | None = None,
    *,
    revision: int = 0,
) -> str:
    """Work a waiting claim as a new attempt, measuring preparation and execution."""
    claim = api.start_claim(claim_id, str(uuid.uuid4()), revision)
    error = run_claim_graph(graph, claim, WORK_CLAIM, run_id, version)
    return settle(claim_id, error, revision)


def carry_on(
    claim: dict[str, Any],
    graph: ClaimGraph | None,
    run_id: str | None = None,
    version: int | None = None,
) -> str:
    """Resume the existing thread and attempt, measuring checkpoint lookup too."""
    decision = claim["decision"]
    if decision and decision["signed_off_by"] and not claim["payment"]:
        return report(claim, "")
    error = run_claim_graph(graph, claim, CARRY_ON, run_id, version)
    return settle(claim["claim_id"], error, claim["revision"])
