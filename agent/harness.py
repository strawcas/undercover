"""Build and execute the durable claim graph, with one saver per execution."""

import contextvars
import json
import logging
import uuid
from collections.abc import Callable
from functools import cache, partial
from http import HTTPStatus
from threading import Condition
from time import monotonic
from typing import Any, cast

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
from langgraph.types import Command, interrupt

from agent import api, checkpoints, measurements, models, screening
from agent.backend_client import Refused
from agent.tools import CLAIM_TOOLS, ClaimContext, ClaimState

log = logging.getLogger(__name__)

ClaimGraph = CompiledStateGraph[ClaimState, ClaimContext, Any, Any]
Messages = dict[str, list[BaseMessage]]
Start = Messages | Command | None

CLAIM_MODEL_CALLS = 30
NO_DECISION = "The agent finished without recording a decision"
WORK_CLAIM = "Work claim"
CARRY_ON = "Carry on"
SCREEN = "screen_claim"
RECORD_SCREEN = "record_screen"
AGENT = "agent"
TOOLS = "tools"
PAUSE = "wait_for_sign_off"
PAY = "pay"


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


def queue_resume(claim_id: str) -> bool:
    """Reserve a sign-off handoff even while the worker is reaching its pause."""
    with claim_lock:
        if claim_id in pending_resumes:
            return False
        pending_resumes.add(claim_id)
        return True


def take_resume(claim_id: str) -> None:
    """Wait for the current worker, then atomically take its reserved handoff."""
    with claim_lock:
        claim_lock.wait_for(lambda: claim_id not in owned_claims)
        pending_resumes.remove(claim_id)
        owned_claims.add(claim_id)


def execution_graph(version: int | None = None) -> ClaimGraph:
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
    )


def execute_reserved_claim(claim_id: str, run_id: str | None, version: int) -> str:
    """Start or resume an owned claim, retrying ordinary failed attempts fresh."""
    try:
        while True:
            api.admit_claim(claim_id, run_id)
            claim = api.get_claim(claim_id)
            if claim["status"] == api.Status.WAITING:
                line = work_claim(claim_id, None, run_id, version)
            elif claim["status"] == api.Status.IN_PROGRESS:
                line = carry_on(claim, None, run_id, version)
            else:
                return "No eligible work"
            if api.get_claim(claim_id)["status"] != api.Status.WAITING:
                return line
    finally:
        release_claim(claim_id)


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
        return f"Refused with status {error}"
    if isinstance(error, ToolException):
        return f"Refused: {error}"
    raise error


def record_reply(claim_id: str, reply: BaseMessage) -> None:
    """Record the model's reply on the claim: its text, then each tool it calls."""
    if reply.text:
        api.record_step(claim_id, "message", reply.text)
    if isinstance(reply, AIMessage):
        for call in reply.tool_calls:
            args = ", ".join(f"{k}={json.dumps(v)}" for k, v in call["args"].items())
            api.record_step(claim_id, "tool_call", f"{call['name']}({args})")


def record_result(
    request: ToolCallRequest,
    execute: Callable[[ToolCallRequest], ToolMessage | Command],
) -> ToolMessage | Command:
    """Run a tool and record its result on the claim, then any step it gives as its
    artifact, such as a section it read."""
    result = execute(request)
    if isinstance(result, ToolMessage):
        message = result
    else:
        message = cast(dict[str, Any], result.update)["messages"][0]
    claim_id = cast(ClaimContext, request.runtime.context).claim_id
    api.record_step(claim_id, "tool_result", f"{message.name}: {message.text}")
    if message.artifact:
        api.record_step(claim_id, *message.artifact)
    return result


def call_model(
    state: ClaimState,
    runtime: Runtime[ClaimContext],
    llm: Runnable[LanguageModelInput, BaseMessage],
    instructions: Callable[[], SystemMessage],
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
    api.admit_claim(runtime.context.claim_id, runtime.context.run_id)
    reply = llm.invoke([instructions(), *state["messages"]])
    api.record_model_call(
        runtime.context.claim_id,
        measurements.model_reply(reply, runtime.context.run_id),
    )
    record_reply(runtime.context.claim_id, reply)
    return Command(
        update={"messages": [reply], "model_calls": 1},
        goto=TOOLS if isinstance(reply, AIMessage) and reply.tool_calls else END,
    )


def wait_for_sign_off(state: ClaimState, runtime: Runtime[ClaimContext]) -> None:
    """Pause unless this execution has the backend's recorded sign-off."""
    if runtime.context.signed_off_status is None:
        interrupt({"claim_id": runtime.context.claim_id})


def screen_claim(state: ClaimState, runtime: Runtime[ClaimContext]) -> dict[str, Any]:
    """Save the screening result before logging it or recording a referral."""
    context = runtime.context
    return {
        "screening": screening.check(context.claim_id, context.run_id),
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
            context.claim_id, "refer", 0, screening.SECTION, reason
        )
        return {"decision": claim["decision"], "status": claim["status"]}
    return {}


def after_screen(state: ClaimState) -> str:
    """Send only cleared claims to the adjudication model."""
    result = state["screening"]["result"]
    if result is None:
        return SCREEN
    return END if result["instructions_found"] else AGENT


def pay(state: ClaimState, runtime: Runtime[ClaimContext]) -> dict[str, Any]:
    """Preserve a recorded sign-off outcome or request an ordinary payout."""
    if runtime.context.signed_off_status is not None:
        return {"status": runtime.context.signed_off_status}
    context = runtime.context
    try:
        claim = api.pay_claim(context.claim_id, state["decision"]["payout"])
    except Refused as refusal:
        if refusal.status_code != HTTPStatus.FORBIDDEN or context.evaluation_id is None:
            raise
        claim = api.hold_evaluation_payment(context.claim_id)
    return {"status": claim["status"]}


def after_tools(state: ClaimState) -> str:
    """Go back to the model until a decision is recorded, then pay an approval, after
    a sign-off if the backend holds it, or stop."""
    decision, status = state["decision"], state["status"]
    if not decision:
        return AGENT
    if status == api.Status.WAITING_FOR_SIGN_OFF:
        return PAUSE
    if decision["decision"] == api.APPROVE and status == api.Status.IN_PROGRESS:
        return PAY
    return END


def claim_graph(
    llm: BaseChatModel,
    saver: BaseCheckpointSaver[Any],
    configuration: dict[str, Any] | None = None,
) -> ClaimGraph:
    """Build the claim agent's graph, which saves its progress after every step."""

    @cache
    def instructions() -> SystemMessage:
        value = configuration or api.get_current_configuration()
        return SystemMessage(value["prompt"])

    builder = StateGraph(ClaimState, context_schema=ClaimContext)
    builder.add_node(SCREEN, screen_claim)
    builder.add_node(RECORD_SCREEN, record_screen)
    builder.add_node(
        AGENT,
        partial(call_model, llm=llm.bind_tools(CLAIM_TOOLS), instructions=instructions),
        destinations=(SCREEN, RECORD_SCREEN, TOOLS, END),
    )
    builder.add_node(
        TOOLS,
        ToolNode(CLAIM_TOOLS, handle_tool_errors=refusal, wrap_tool_call=record_result),
    )
    builder.add_node(PAUSE, wait_for_sign_off)
    builder.add_node(PAY, pay)
    builder.add_edge(START, SCREEN)
    builder.add_edge(SCREEN, RECORD_SCREEN)
    builder.add_conditional_edges(RECORD_SCREEN, after_screen, [SCREEN, AGENT, END])
    builder.add_conditional_edges(TOOLS, after_tools, [AGENT, PAUSE, PAY, END])
    builder.add_edge(PAUSE, PAY)
    builder.add_edge(PAY, END)
    return builder.compile(checkpointer=saver)


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
        graph = graph or execution_graph(version)
        start = (
            begin(claim["claim_id"])
            if name == WORK_CLAIM
            else resume_input(graph, claim)
        )

        # A fresh context gives every claim its own root trace,
        # which Langfuse needs to put it in the claim's session.
        def execute(ready: ClaimGraph) -> None:
            for update in ready.stream(
                start,
                claim_config(claim, name, run_id),
                context=ClaimContext(
                    claim["claim_id"],
                    claim["thread_id"],
                    run_id,
                    signed_off,
                    claim["evaluation_id"],
                ),
                durability="sync",
                stream_mode="updates",
            ):
                if (signed_off is None and SCREEN not in update) or PAY in update:
                    progress()

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


def settle(claim_id: str, error: str | None) -> str:
    """Fail a claim the agent left undecided, and report how the claim ended. An
    error after the decision is logged, and the claim keeps its decision."""
    claim = api.get_claim(claim_id)
    reason = error or NO_DECISION
    if claim["status"] == api.Status.IN_PROGRESS and claim["decision"] is None:
        claim = api.fail_claim(claim_id, reason)
    elif error:
        log.warning("%s  %s", claim_id, error)
    line = report(claim, reason)
    log.info(line)
    return line


def resume_input(graph: ClaimGraph, claim: dict[str, Any]) -> Start:
    """Resume a paused checkpoint or restart a thread with no saved first step."""
    if paid_on_sign_off(claim):
        return Command(resume=True)
    saved = graph.get_state(thread_of(claim))
    if saved.next == (PAUSE,):
        return Command(resume=True)
    if saved.created_at is None and claim["decision"] is None:
        return begin(claim["claim_id"])
    return None


def paid_on_sign_off(claim: dict[str, Any]) -> bool:
    """Use the recorded payment to recognize a completed senior settlement."""
    return bool(claim["payment"] and claim["payment"]["signed_off_by"])


def work_claim(
    claim_id: str,
    graph: ClaimGraph | None,
    run_id: str | None = None,
    version: int | None = None,
) -> str:
    """Work a waiting claim as a new attempt, measuring preparation and execution."""
    claim = api.start_claim(claim_id, str(uuid.uuid4()))
    error = run_claim_graph(graph, claim, WORK_CLAIM, run_id, version)
    return settle(claim_id, error)


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
    return settle(claim["claim_id"], error)
