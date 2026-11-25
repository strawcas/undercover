"""The Copilot's handler-authorized claim, customer and run tools."""

from typing import Any

from langchain.tools import ToolException, ToolRuntime, tool

from agent.copilot import api
from agent.copilot.state import Context


@tool
def list_claims(runtime: ToolRuntime[Context]) -> list:
    """List original queue claims with their current status and outcome."""
    return runtime.context.progress.app.read(api.CLAIMS)


@tool
def get_claim(claim_id: str, runtime: ToolRuntime[Context]) -> dict:
    """Read one original queue claim, its decision, payment and earlier reviews."""
    return original_claim(runtime.context.progress.app, claim_id)


def original_claim(app: api.HandlerClient, claim_id: str) -> dict[str, Any]:
    """Resolve an original claim for a handler read or review."""
    claim = app.read(api.record(api.CLAIMS, claim_id))
    if claim["evaluation_id"] is not None:
        raise ToolException("This tool reads original queue claims")
    return claim


@tool
def get_claim_steps(claim_id: str, runtime: ToolRuntime[Context]) -> list:
    """Read the claim agent's recorded steps for an original queue claim."""
    original_claim(runtime.context.progress.app, claim_id)
    return runtime.context.progress.app.read(
        f"{api.record(api.CLAIMS, claim_id)}/steps"
    )


@tool
def search_customers(name: str, runtime: ToolRuntime[Context]) -> list:
    """Find customers by name and return matching customer IDs."""
    return runtime.context.progress.app.read(api.CUSTOMERS, params={"name": name})


@tool
def get_customer(customer_id: str, runtime: ToolRuntime[Context]) -> dict:
    """Look up a customer ID, policies, prior claims and current queue claims."""
    app = runtime.context.progress.app
    path = api.record(api.CUSTOMERS, customer_id)
    return app.read(path) | {"previous_claims": app.read(f"{path}/claims")}


@tool
def get_policy(policy_id: str, runtime: ToolRuntime[Context]) -> dict:
    """Read a policy by ID, including its cover, travelers and trip."""
    return runtime.context.progress.app.read(api.record("/policies", policy_id))


@tool
def latest_dispatch(runtime: ToolRuntime[Context]) -> dict | None:
    """Read the latest dispatch and what currently needs a handler."""
    app = runtime.context.progress.app
    latest = app.read("/runs/latest")
    return app.read(api.record(api.DISPATCHES, latest["run_id"])) if latest else None


@tool
def get_dispatch(run_id: str, runtime: ToolRuntime[Context]) -> dict:
    """Read a dispatch by run ID and its handoff to handlers."""
    return runtime.context.progress.app.read(api.record(api.DISPATCHES, run_id))


@tool
def list_evaluations(runtime: ToolRuntime[Context]) -> list:
    """List evaluations with their aggregate measures."""
    return runtime.context.progress.app.read(api.EVALUATIONS)


def evaluation(app: api.HandlerClient, run_id: str) -> dict[str, Any]:
    """Read aggregate appraisal of a specific evaluation without labels."""
    return app.read(f"{api.record(api.EVALUATIONS, run_id)}/measures")


@tool
def get_evaluation(run_id: str, runtime: ToolRuntime[Context]) -> dict:
    """Read one evaluation's status and aggregate measures by run ID."""
    return evaluation(runtime.context.progress.app, run_id)


@tool
def get_settings(runtime: ToolRuntime[Context]) -> dict:
    """Read the approval limit, objectives and default claim-agent concurrency."""
    values = runtime.context.progress.app.read(api.SETTINGS)["values"]
    return {
        field: values[field]
        for field in ("payout_approval_limit", "objectives", "concurrency")
    }


READ_TOOLS = (
    (list_claims, "claim"),
    (get_claim, "claim"),
    (get_claim_steps, "claim"),
    (search_customers, "customer"),
    (get_customer, "customer"),
    (get_policy, "customer"),
    (latest_dispatch, "work"),
    (get_dispatch, "work"),
    (list_evaluations, "work"),
    (get_evaluation, "work"),
    (get_settings, "work"),
)
