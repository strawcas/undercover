"""A model-free LangGraph dispatcher, following the Week 2 Day 4 lab."""

import operator
from functools import partial
from typing import Annotated, Any, TypedDict, cast

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, Send

from agent import api, harness


class Dispatch(TypedDict):
    run_id: str
    claims: list[str]
    results: Annotated[list[str], operator.add]


class Work(TypedDict):
    run_id: str
    claim_id: str


def list_work(state: Dispatch) -> dict[str, list[str]]:
    """Select ordinary eligible claims or an evaluation's existing copies."""
    run = api.get_run(state["run_id"])
    if run["kind"] == "evaluation":
        ids = [member["claim_id"] for member in run["members"]]
    else:
        claims = sorted(
            api.list_claims(), key=lambda c: (c["submitted_on"], c["claim_id"])
        )
        ids = [
            c["claim_id"]
            for c in claims
            if c["status"] in (api.Status.WAITING, api.Status.IN_PROGRESS)
            and not harness.is_reserved(c["claim_id"])
        ]
        for claim_id in ids:
            api.record_run_claim(state["run_id"], claim_id, "selected", None)
    return {"claims": ids}


def fan_out(state: Dispatch) -> list[Send] | str:
    """Fan out once per claim; an empty queue finishes cleanly."""
    return [
        Send("work_claim", {"claim_id": c, "run_id": state["run_id"]})
        for c in state["claims"]
    ] or END


def work_claim(state: Work, *, completed: list[str]) -> dict[str, list[str]]:
    """Work through the harness boundary, retaining one member through retries."""
    line = harness.execute_claim(state["claim_id"], state["run_id"])
    status = "skipped" if line.startswith("Skipped:") else "finished"
    api.record_run_claim(state["run_id"], state["claim_id"], status, None)
    completed.append(state["claim_id"])
    return {"results": [state["claim_id"]]}


def exhausted(
    state: Work, error: NodeError, *, recovered: list[Exception], completed: list[str]
) -> Command:
    """Retain unresolved work after retries and let every other worker complete."""
    if not isinstance(error.error, Exception):
        raise error.error
    message = harness.describe(error.error)
    claim = api.get_claim(state["claim_id"])
    if claim["thread_id"]:
        api.record_step(state["claim_id"], "error", message)
    api.record_run_claim(state["run_id"], state["claim_id"], "in_progress", message)
    recovered.append(error.error)
    completed.append(state["claim_id"])
    return Command(update={"results": [state["claim_id"]]}, goto=END)


def dispatch(run_id: str, concurrency: int) -> dict[str, Any]:
    """Work a run with bounded parallelism and transient-only outer retries."""
    builder = StateGraph(Dispatch)
    recovered: list[Exception] = []
    completed: list[str] = []
    builder.add_node("list_claims", list_work)
    builder.add_node(
        "work_claim",
        partial(work_claim, completed=completed),
        retry_policy=RetryPolicy(
            max_attempts=3, retry_on=(ModelRateLimitError, ModelTimeoutError)
        ),
        error_handler=cast(
            Any, partial(exhausted, recovered=recovered, completed=completed)
        ),
    )
    builder.add_edge(START, "list_claims")
    builder.add_conditional_edges("list_claims", fan_out, ["work_claim", END])
    builder.add_edge("work_claim", END)
    result: dict[str, Any] = {}
    try:
        for state in builder.compile().stream(
            {"run_id": run_id, "claims": [], "results": []},
            {"max_concurrency": concurrency},
            stream_mode="values",
        ):
            result = state
    except Exception as error:
        # LangGraph 1.2.12 rethrows handled parallel futures on executor exit (#8277).
        complete = sorted(completed) == sorted(result.get("claims", []))
        if not complete or not any(error is handled for handled in recovered):
            raise
        result["results"] = completed
    return result
