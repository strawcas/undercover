"""Render UnderCover's pilot success criteria from recorded measures."""

import json
from typing import Any

import httpx
from langchain.tools import ToolException, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from agent.backend_client import Refused
from agent.copilot import actions, api
from agent.copilot.state import Context

STRAIGHT_THROUGH = 0.70


def measure(label: str, target: float, value: float | None) -> tuple[str, bool | None]:
    """Format one percentage criterion with a recorded verdict."""
    actual, met = (
        ("Unavailable", None) if value is None else (f"{value:.1%}", value >= target)
    )
    status = "Unavailable" if met is None else "Met" if met else "Missed"
    return f"| {label} | {target:.1%} | {actual} | {status} |", met


def table(accuracy: float, run: dict[str, Any], coverage: dict[str, Any]) -> str:
    """Compute the recorded figures and render their verdicts."""
    scores = run["scores"] if run["status"] == "finished" else {}
    straight, straight_met = measure(
        "Straight through", STRAIGHT_THROUGH, scores.get("straight_through")
    )
    accurate, accuracy_met = measure(
        "Accuracy within $1", accuracy, scores.get("accuracy")
    )
    paid, signed = coverage["paid"], coverage["signed_off"]
    signoff_met = paid == signed
    limit = f"${coverage['limit']:,.2f}"
    result = (
        "No payouts above the limit" if paid == 0 else f"{signed} of {paid} signed off"
    )
    outcome = "Met" if signoff_met else "Missed"
    signoffs = f"| Payout sign-off | Every payout over {limit} | {result} | {outcome} |"
    verdicts = (straight_met, accuracy_met, signoff_met)
    overall = (
        "We are meeting all three criteria."
        if all(verdicts)
        else "We are missing at least one criterion."
        if False in verdicts
        else "We cannot yet verify all three criteria."
    )
    note = run.get("reason") or run.get("stop_reason") or ""
    return "\n".join(
        [
            "| Criterion | Target | Recorded result | Status |",
            "| --- | --- | --- | --- |",
            straight,
            accurate,
            signoffs,
            "",
            overall,
            *([f"\nEvaluation: {note}"] if note else []),
        ]
    )


def assess(context: Context) -> tuple[str, str]:
    """Start one whole held-out run and retain payout evidence even if it fails."""
    app = context.progress.app
    settings = app.read(api.SETTINGS)["values"]
    try:
        run_id = actions.start_run(
            context,
            api.EVALUATIONS,
            settings["concurrency"],
            purpose="pilot-criteria",
            set_name="held_out",
            tiny=False,
        )
        result = actions.wait(context, run_id, evaluation=True)
    except (Refused, ToolException) as error:
        result = {"status": "failed", "reason": str(error)}
    except httpx.RequestError:
        result = {"status": "failed", "reason": "The evaluation could not be verified."}
    coverage = app.read(f"{api.SETTINGS}/payout-coverage")
    return table(settings["objectives"]["accuracy"], result, coverage), result["status"]


@tool
def check_success_criteria(runtime: ToolRuntime[Context]) -> Command:
    """Evaluate the three agreed pilot criteria on the whole held-out set."""
    rendered, status = assess(runtime.context)
    return Command(
        update={
            "criteria": rendered,
            "messages": [
                ToolMessage(
                    json.dumps({"evaluation": status, "assessment": rendered}),
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )
