"""The harness: a queue agent works the run, and a claim graph works each claim."""

import contextvars
import json
import logging
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from time import monotonic
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolErrorMiddleware,
)
from langchain.tools import ToolException
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_openai import ChatOpenAI
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from psycopg import Connection, sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool
from pydantic import SecretStr

from agent import api
from agent.tools import CLAIM_TOOLS, QUEUE_TOOLS, ClaimContext, ClaimState, RunContext

log = logging.getLogger(__name__)

ClaimGraph = CompiledStateGraph[ClaimState, ClaimContext, Any, Any]
QueueAgent = CompiledStateGraph[Any, RunContext, Any, Any]
Messages = dict[str, list[BaseMessage]]
Start = Messages | Command | None

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
SCHEMA = "undercover"
CONNECTIONS = 4
CLAIM_MODEL_CALLS = 30
QUEUE_MODEL_CALLS_PER_CLAIM = 6
QUEUE_MODEL_CALLS_TO_PLAN = 12
REMINDERS = 2
NO_DECISION = "The agent finished without recording a decision"
NO_SUMMARY = "The run finished without a summary."
RUN_STOPPED = "The run stopped before it finished."
WORK_CLAIM = "Work claim"
CARRY_ON = "Carry on"
AGENT = "agent"
TOOLS = "tools"
PAUSE = "wait_for_sign_off"
PAY = "pay"


class TooManyModelCalls(Exception):
    """The claim agent made as many model calls as it may without finishing."""


def model_settings() -> dict[str, Any]:
    """Resolve the model and reasoning effort once for execution and run records."""
    name = os.getenv("MODEL_NAME")
    return {"model": name or DEFAULT_MODEL, "reasoning_effort": None if name else "low"}


def model() -> ChatOpenAI:
    """Return the configured model through OpenRouter."""
    return ChatOpenAI(
        **model_settings(),
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        timeout=120,
        max_retries=5,
    )


@contextmanager
def checkpointer() -> Iterator[PostgresSaver]:
    """Save the claim graph's progress in Postgres at DATABASE_URL, in our own schema,
    with at most CONNECTIONS connections."""
    settings = {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": f"-c search_path={SCHEMA}",
    }
    with ConnectionPool[Connection[DictRow]](
        os.environ["DATABASE_URL"],
        min_size=1,
        max_size=CONNECTIONS,
        kwargs=settings,
    ) as pool:
        with pool.connection() as connection:
            create = sql.SQL("CREATE SCHEMA IF NOT EXISTS {}")
            connection.execute(create.format(sql.Identifier(SCHEMA)))
        saver = PostgresSaver(pool)
        saver.setup()
        yield saver


def refusal(error: Exception) -> str:
    """Show the model why a tool was refused, and let any other error stop the agent."""
    if isinstance(error, api.Refused):
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
    instructions: SystemMessage,
) -> dict[str, Any]:
    """Ask the model for its next step, up to CLAIM_MODEL_CALLS times in all, and
    record its reply on the claim."""
    calls = state["model_calls"]
    if calls >= CLAIM_MODEL_CALLS:
        message = f"The claim agent made {calls} model calls without finishing"
        raise TooManyModelCalls(message)
    reply = llm.invoke([instructions, *state["messages"]])
    usage = reply.usage_metadata if isinstance(reply, AIMessage) else None
    reported = reply.response_metadata.get("token_usage", {})
    api.record_model_call(
        runtime.context.claim_id,
        {
            "reply_id": reply.id or str(uuid.uuid4()),
            "run_id": runtime.context.run_id,
            "input_tokens": usage.get("input_tokens") if usage else None,
            "output_tokens": usage.get("output_tokens") if usage else None,
            "total_tokens": usage.get("total_tokens") if usage else None,
            "cost": reported.get("cost"),
        },
    )
    record_reply(runtime.context.claim_id, reply)
    return {"messages": [reply], "model_calls": 1}


def wait_for_sign_off(state: ClaimState, runtime: Runtime[ClaimContext]) -> None:
    """Pause until a handler signs off the payout, which the backend records. On
    resume this runs again from the top, so nothing comes before the interrupt."""
    interrupt({"claim_id": runtime.context.claim_id})


def pay(state: ClaimState, runtime: Runtime[ClaimContext]) -> dict[str, Any]:
    """Pay the approved payout, which the backend refuses unless it may be paid."""
    claim = api.pay_claim(runtime.context.claim_id, state["decision"]["payout"])
    return {"status": claim["status"]}


def after_agent(state: ClaimState) -> str:
    """Run the tools the model asked for, or stop: it finished without a decision."""
    reply = state["messages"][-1]
    return TOOLS if isinstance(reply, AIMessage) and reply.tool_calls else END


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


def claim_graph(llm: BaseChatModel, saver: BaseCheckpointSaver[Any]) -> ClaimGraph:
    """Build the claim agent's graph, which saves its progress after every step."""
    instructions = SystemMessage(api.get_instructions()["claim"])
    builder = StateGraph(ClaimState, context_schema=ClaimContext)
    builder.add_node(
        AGENT,
        partial(call_model, llm=llm.bind_tools(CLAIM_TOOLS), instructions=instructions),
    )
    builder.add_node(
        TOOLS,
        ToolNode(CLAIM_TOOLS, handle_tool_errors=refusal, wrap_tool_call=record_result),
    )
    builder.add_node(PAUSE, wait_for_sign_off)
    builder.add_node(PAY, pay)
    builder.add_edge(START, AGENT)
    builder.add_conditional_edges(AGENT, after_agent, [TOOLS, END])
    builder.add_conditional_edges(TOOLS, after_tools, [AGENT, PAUSE, PAY, END])
    builder.add_edge(PAUSE, PAY)
    builder.add_edge(PAY, END)
    return builder.compile(checkpointer=saver)


def thread_of(claim: dict[str, Any]) -> RunnableConfig:
    """Address the claim's thread, where the claim graph saves its progress."""
    return {"configurable": {"thread_id": claim["thread_id"]}}


def claim_config(claim: dict[str, Any], name: str) -> RunnableConfig:
    """Run on the claim's thread, traced in Langfuse in the session of that thread."""
    session = f"{claim['claim_id']}:{claim['thread_id']}"
    return thread_of(claim) | {
        "callbacks": [CallbackHandler()],
        "metadata": {"langfuse_session_id": session, "langfuse_trace_name": name},
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
    graph: ClaimGraph,
    start: Start,
    claim: dict[str, Any],
    name: str,
    run_id: str | None = None,
) -> str | None:
    """Run the claim graph on the claim's thread, saving each step, and say what went
    wrong, if anything."""
    slice_id, started = str(uuid.uuid4()), monotonic()

    def progress() -> None:
        api.record_work_slice(
            claim["claim_id"], slice_id, run_id, monotonic() - started
        )

    error = None
    try:
        progress()

        # A fresh context makes this a root run even inside the queue agent's tool,
        # which Langfuse needs to put it in the claim's session.
        def execute() -> None:
            for _ in graph.stream(
                start,
                claim_config(claim, name),
                context=ClaimContext(claim["claim_id"], run_id),
                durability="sync",
                stream_mode="updates",
            ):
                progress()

        contextvars.Context().run(execute)
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


def work_claim(claim_id: str, graph: ClaimGraph, run_id: str | None = None) -> str:
    """Work one claim from a clean start, as a new attempt, and report how it ended."""
    claim = api.start_claim(claim_id, str(uuid.uuid4()))
    error = run_claim_graph(graph, begin(claim_id), claim, WORK_CLAIM, run_id)
    return settle(claim_id, error)


def carry_on(claim_id: str, graph: ClaimGraph, run_id: str | None = None) -> str:
    """Carry a claim in progress on from its saved progress, as the same attempt, and
    report how it ended. A claim at the pause has been signed off, so it resumes past
    it. One with nothing saved, stopped before its first step, starts on its own
    thread; any other carries on, finishing a step whose writes were saved."""
    claim = api.get_claim(claim_id)
    saved = graph.get_state(thread_of(claim))
    start: Start = None
    if saved.next == (PAUSE,):
        start = Command(resume=True)
    elif saved.created_at is None and claim["decision"] is None:
        start = begin(claim_id)
    error = run_claim_graph(graph, start, claim, CARRY_ON, run_id)
    return settle(claim_id, error)


def waiting() -> list[str]:
    """Return the ids of the claims waiting in the queue, oldest submitted first."""
    claims = sorted(api.list_claims(), key=lambda claim: claim["submitted_on"])
    return [c["claim_id"] for c in claims if c["status"] == api.Status.WAITING]


def finish_stopped_run() -> None:
    """Finish the latest run if it stopped before it finished."""
    run = api.get_latest_run()
    if run is not None and run["status"] == api.RunStatus.RUNNING:
        api.finish_run(run["run_id"], RUN_STOPPED)


def carry_on_stopped(graph: ClaimGraph) -> None:
    """Carry on every claim in progress: stopped partway, or signed off while the
    agent was away."""
    for claim in api.list_claims():
        if claim["status"] == api.Status.IN_PROGRESS:
            carry_on(claim["claim_id"], graph)


def missed(run: RunContext) -> list[str]:
    """Return the waiting claims the run should still work: its own, then new ones."""
    ids = waiting()
    own = [claim_id for claim_id in ids if claim_id in run.started]
    new = [claim_id for claim_id in ids if claim_id not in run.started]
    return own + new[: run.cap - len(run.started)]


def queue_agent(llm: BaseChatModel, cap: int) -> QueueAgent:
    """Build the queue agent for a run, which keeps its conversation between turns."""
    calls = QUEUE_MODEL_CALLS_PER_CLAIM * cap + QUEUE_MODEL_CALLS_TO_PLAN
    middleware: list[AgentMiddleware[Any, Any]] = [
        ModelCallLimitMiddleware(run_limit=calls, exit_behavior="error"),
        ToolErrorMiddleware(lambda error, _: refusal(error)),
    ]
    return create_agent(
        llm,
        tools=QUEUE_TOOLS,
        system_prompt=api.get_instructions()["queue"],
        middleware=middleware,
        context_schema=RunContext,
        checkpointer=InMemorySaver(),
    )


def work_queue(run: RunContext, agent: QueueAgent) -> str:
    """Let the queue agent work the run, naming any claims it missed, and summarize."""
    # One tool call at a time, so the cap holds when several claims are handed over.
    config: RunnableConfig = {
        "configurable": {"thread_id": run.run_id},
        "max_concurrency": 1,
    }
    message = f"Please work the queue. This run can work up to {run.cap} claims."
    state = agent.invoke(ask(message), config, context=run)
    for _ in range(REMINDERS):
        left = missed(run)
        if not left:
            break
        reminder = (
            "These claims are still waiting within the run's cap: "
            f"{', '.join(left)}. Please work them, then finish with the summary."
        )
        state = agent.invoke(ask(reminder), config, context=run)
    return state["messages"][-1].text or NO_SUMMARY


def run_queue(cap: int | None, llm: BaseChatModel, graph: ClaimGraph) -> None:
    """Finish a run that stopped and carry on every claim in progress, then work the
    queue with a queue agent, up to cap claims or every waiting claim."""
    finish_stopped_run()
    carry_on_stopped(graph)
    count = len(waiting())
    if count == 0:
        log.info("No claims are waiting")
        return
    cap = min(cap or count, count)
    run_id = api.start_run(cap)["run_id"]
    run = RunContext(run_id, cap, partial(work_claim, graph=graph, run_id=run_id))
    try:
        summary = work_queue(run, queue_agent(llm, cap))
    except Exception as error:
        api.finish_run(run_id, f"The run stopped: {describe(error)}")
        raise
    api.finish_run(run_id, summary)
    log.info(summary)
