"""Handler actions and the revision-bound confirmation nodes."""

import json
from time import monotonic, sleep
from typing import Annotated, Any

import httpx
from langchain.tools import ToolException, ToolRuntime, tool
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from pydantic import Field

from agent.copilot import api, progress, reads
from agent.copilot.state import Context, State

CONCURRENCY = Annotated[int | None, Field(ge=1, le=25)]
WAIT_LIMIT = 15 * 60
POLL_SECONDS = 3
PREPARE = "prepare_sign_off"
CANCELED = "I haven’t signed off or paid this claim. What would you like me to do next?"


def concurrency(app: api.HandlerClient, requested: int | None) -> int:
    """Resolve Settings only when the handler did not name a concurrency."""
    return (
        requested
        if requested is not None
        else app.read(api.SETTINGS)["values"]["concurrency"]
    )


def start_run(
    context: Context,
    path: str,
    concurrency_value: int | None,
    *,
    purpose: str = "",
    **selection: Any,
) -> str:
    """Start an admitted decision run through the handler's existing API."""
    app = context.progress.app
    values = {"concurrency": concurrency(app, concurrency_value), **selection}
    action = purpose or (path + json.dumps(values, sort_keys=True))
    headers = context.progress.action_headers(action)
    try:
        value = app.call("POST", path, json=values, headers=headers)
    except httpx.RequestError:
        value = app.call("POST", path, json=values, headers=headers)
    return value["run_id"]


def poll(context: Context, run_id: str, *, evaluation: bool) -> dict[str, Any]:
    """Wait on this run in code and preserve a usable link on timeout."""
    app = context.progress.app
    destination = "the Scoreboard" if evaluation else "the Claims Queue"
    deadline = monotonic() + WAIT_LIMIT
    while monotonic() < deadline:
        run = (
            reads.evaluation(app, run_id)
            if evaluation
            else app.read(api.record(api.DISPATCHES, run_id))
        )
        if run["status"] != "running":
            return run
        sleep(min(POLL_SECONDS, max(0, deadline - monotonic())))
    return {
        "run_id": run_id,
        "status": "timed_out",
        "reason": (
            f"It is still running after 15 minutes. Follow it on {destination}."
        ),
    }


def wait(context: Context, run_id: str, *, evaluation: bool) -> dict[str, Any]:
    """Expose a durable working card while polling this dispatch or evaluation."""
    tool_name = "wait_for_evaluation" if evaluation else "wait_for_dispatch"
    return progress.observe(
        context.progress,
        tool_name,
        "work",
        run_id,
        lambda: poll(context, run_id, evaluation=evaluation),
    )


@tool
def start_dispatch(
    number_of_agents: CONCURRENCY,
    wait_for_result: bool,
    runtime: ToolRuntime[Context],
) -> dict:
    """Start queue work, using null for Settings and optionally waiting."""
    context = runtime.context
    run = start_run(context, api.DISPATCHES, number_of_agents)
    return (
        wait(context, run, evaluation=False)
        if wait_for_result
        else {"status": "accepted", "run_id": run}
    )


@tool
def start_evaluation(
    set_name: Annotated[str, Field(pattern="^(evaluation|held_out)$")],
    tiny: bool,
    number_of_agents: CONCURRENCY,
    wait_for_result: bool,
    runtime: ToolRuntime[Context],
) -> dict:
    """Start a tiny or whole evaluation, optionally waiting for results."""
    context = runtime.context
    run = start_run(
        context, api.EVALUATIONS, number_of_agents, set_name=set_name, tiny=tiny
    )
    return (
        wait(context, run, evaluation=True)
        if wait_for_result
        else {"status": "accepted", "run_id": run}
    )


def senior(context: Context) -> None:
    """Refuse review before preparing an action for an ordinary handler."""
    if not context.handler["can_approve_over_limit"]:
        raise ToolException(
            f"{context.handler['name']} cannot perform this action. "
            "A senior handler is required."
        )


@tool
def send_back(claim_id: str, note: str, runtime: ToolRuntime[Context]) -> dict:
    """Return a held original claim for review with the note the handler requested."""
    context = runtime.context
    senior(context)
    app = context.progress.app
    claim = reads.original_claim(app, claim_id)
    headers = context.progress.action_headers(f"send-back:{claim_id}:{note}")
    try:
        return app.review(claim_id, "send-back", claim["revision"], headers, note=note)
    except httpx.RequestError:
        return app.review(claim_id, "send-back", claim["revision"], headers, note=note)


@tool
def prepare_sign_off(claim_id: str, runtime: ToolRuntime[Context]) -> Command:
    """Prepare the confirmation card for signing off one held original payout."""
    context = runtime.context
    senior(context)
    claim = reads.original_claim(context.progress.app, claim_id)
    if not claim["sign_off_available"]:
        raise ToolException(f"Claim {claim_id} is not waiting for sign-off.")
    decision = claim["decision"]
    intent = {
        "claim_id": claim["claim_id"],
        "customer_name": claim["customer_name"],
        "amount": decision["payout"],
        "reason": decision["reason"],
        "revision": claim["revision"],
    }
    return Command(
        update={
            "intent": intent,
            "messages": [
                ToolMessage(json.dumps(intent), tool_call_id=runtime.tool_call_id)
            ],
        }
    )


def confirm(state: State) -> dict[str, Any]:
    """Pause on the saved intent until the handler answers the app's card."""
    return {"confirmed": interrupt(state.get("intent"))}


def settle(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    """Apply an accepted confirmation once against the held decision revision."""
    if not state.get("confirmed"):
        return {"messages": [AIMessage(CANCELED)], "intent": None}
    context = runtime.context
    intent = state.get("intent")
    if intent is None:
        raise RuntimeError("The confirmation is missing its decision")
    claim_id = intent["claim_id"]
    try:
        result = progress.observe(
            context.progress,
            "sign_off_and_pay",
            "action",
            claim_id,
            lambda: pay(context, intent),
        )
    except ToolException as error:
        answer = f"I couldn’t confirm payment for {claim_id}. {error}"
    else:
        answer = (
            f"I signed off {claim_id} and paid ${result['payment']['amount']:,.2f}."
        )
    return {"messages": [AIMessage(answer)], "intent": None}


def pay(context: Context, intent: dict[str, Any]) -> dict[str, Any]:
    """Settle a confirmed payout, checking its record after a lost reply."""
    app = context.progress.app
    claim_id, revision = intent["claim_id"], intent["revision"]
    try:
        return app.review(
            claim_id, "sign-off", revision, context.progress.action_headers("sign-off")
        )
    except httpx.RequestError:
        claim = reads.original_claim(app, claim_id)
        payment = claim["payment"]
        if (
            payment
            and claim["revision"] == revision
            and (payment["signed_off_by"] == context.handler["name"])
        ):
            return claim
        raise


ACTION_TOOLS = (
    (start_dispatch, "action"),
    (start_evaluation, "action"),
    (send_back, "action"),
    (prepare_sign_off, "action"),
)
