"""The UnderCover backend's API: one function for each operation the agent uses."""

import json
from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from agent import backend_client, gateway

backend = backend_client.client()

DecisionKind = Literal["approve", "decline", "refer"]
APPROVE: DecisionKind = "approve"
StepKind = Literal[
    "tool_call",
    "tool_result",
    "message",
    "reading",
    "error",
    "security",
    "refusal",
    "lookup",
    "memory_search",
]
StepContent = str | dict[str, Any]
Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]


class Status(StrEnum):
    """Where a claim stands."""

    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_SIGN_OFF = "waiting_for_sign_off"
    DECIDED = "decided"
    LEFT_FOR_HANDLER = "left_for_handler"


class RunStatus(StrEnum):
    """Where a dispatch or evaluation stands."""

    RUNNING = "running"
    FINISHED = "finished"
    STOPPED = "stopped"


class DecisionInput(TypedDict):
    """The decision fields sent to the app."""

    decision: DecisionKind
    payout: float
    section: str
    reason: str
    decided_by: str
    thread_id: str


LineKind = Literal[
    "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
]


class Line(BaseModel):
    """One covered item or expense on a claim."""

    description: str = Field(description="What the item or expense is")
    amount: float = Field(
        description="The total claimed for the line in US dollars, before any limits. "
        "For several items, the total for all of them"
    )
    quantity: int = Field(
        ge=1,
        description="How many separate items the line holds, such as 3 for three "
        "shirts. A pair or a set is one item, and an expense is 1",
    )
    kind: LineKind = Field(
        description="item: a belonging, or a pair or set that counts as one item. "
        "electronics: only a phone, tablet, laptop, camera or its lens, e-reader or "
        "smartwatch; any other device is an item. "
        "cash: cash that was lost or stolen. "
        "expense: any other cost with a receipt, such as a medical bill, a meal, a "
        "taxi, a fee or a prepaid trip cost. "
        "hotel: a hotel during a trip delay. "
        "meals_without_receipts: meals during a trip delay with no receipts."
    )
    nights: int = Field(
        description="For a hotel, the number of nights, and 0 otherwise"
    )
    hours: float = Field(
        description="For meals without receipts, the length of the delay in hours, "
        "and 0 otherwise"
    )


def _call(method: str, path: str, **request: Any) -> Any:
    """Send one request to the backend and return its JSON, or raise Refused."""
    return backend_client.call(backend, method, path, **request)


def list_claims() -> list[dict[str, Any]]:
    return _call("GET", "/claims")


def get_claim(claim_id: str, *, via: gateway.Gateway | None = None) -> dict[str, Any]:
    if via:
        return via.call(gateway.APP, "get_claim", claim_id=claim_id)
    return _call("GET", f"/claims/{claim_id}")


def claim_text(claim_id: str, *, via: gateway.Gateway | None = None) -> str:
    """Serialize the same claim consistently on either route."""
    claim = get_claim(claim_id, via=via) if via else get_claim(claim_id)
    return json.dumps(claim, ensure_ascii=False, sort_keys=True)


def get_policy(policy_id: str, *, via: gateway.Gateway | None = None) -> dict[str, Any]:
    if via:
        return via.call(gateway.APP, "get_policy", policy_id=policy_id)
    return _call("GET", f"/policies/{policy_id}")


def get_claim_history(
    customer_id: str, *, via: gateway.Gateway | None = None
) -> list[dict[str, Any]]:
    if via:
        return via.call(gateway.APP, "get_claim_history", customer_id=customer_id)
    return _call("GET", f"/customers/{customer_id}/claims")


def get_current_configuration() -> dict[str, Any]:
    return _call("GET", "/configurations/current")


def get_configuration(version: int) -> dict[str, Any]:
    return _call("GET", f"/configurations/{version}")


class PayoutInput(TypedDict):
    """The payout request with its bound attempt."""

    benefit: Benefit
    lines: list[dict[str, Any]]
    paid_by_others: float
    thread_id: str


def calculate_payout(
    claim_id: str, body: PayoutInput, *, via: gateway.Gateway | None = None
) -> dict[str, Any]:
    if via:
        return via.call(gateway.APP, "calculate_payout", claim_id=claim_id, **body)
    return _call("POST", f"/claims/{claim_id}/payout", json=body)


def start_claim(claim_id: str, thread_id: str) -> dict[str, Any]:
    return _call("POST", f"/claims/{claim_id}/start", json={"thread_id": thread_id})


def list_pending_memory() -> list[str]:
    return _call("GET", "/agent/pending-memory")


def prepare_memory(claim_id: str, thread_id: str) -> None:
    _call("POST", f"/claims/{claim_id}/prepare-memory", json={"thread_id": thread_id})


def finish_memory(
    claim_id: str, thread_id: str, messages: list[dict[str, str]]
) -> None:
    _call(
        "POST",
        f"/claims/{claim_id}/finish-memory",
        json={"thread_id": thread_id, "messages": messages},
    )


def admit_claim(claim_id: str, run_id: str | None) -> dict[str, str]:
    return _call("POST", f"/claims/{claim_id}/admission", json={"run_id": run_id})


def stop_run(run_id: str) -> dict[str, Any]:
    return _call("POST", f"/runs/{run_id}/stop")


def record_step(
    claim_id: str,
    kind: StepKind,
    content: StepContent,
    *,
    thread_id: str,
) -> dict[str, Any]:
    body = {"kind": kind, "content": content}
    body["thread_id"] = thread_id
    return _call("POST", f"/claims/{claim_id}/steps", json=body)


def record_decision(
    claim_id: str, body: DecisionInput, *, via: gateway.Gateway | None = None
) -> dict[str, Any]:
    if via:
        return via.call(gateway.APP, "record_decision", claim_id=claim_id, **body)
    return _call("POST", f"/claims/{claim_id}/decision", json=body)


def pay_claim(
    claim_id: str, amount: float, *, via: gateway.Gateway | None = None
) -> dict[str, Any]:
    if via:
        return via.call(gateway.APP, "pay_claim", claim_id=claim_id, amount=amount)
    return _call("POST", f"/claims/{claim_id}/payment", json={"amount": amount})


def hold_limit_payment(claim_id: str) -> dict[str, Any]:
    return _call("POST", f"/claims/{claim_id}/hold-limit-payment")


def hold_gateway_payment(claim_id: str, reason: str) -> dict[str, Any]:
    return _call(
        "POST", f"/claims/{claim_id}/hold-gateway-payment", json={"reason": reason}
    )


def fail_claim(claim_id: str, reason: str) -> dict[str, Any]:
    return _call("POST", f"/claims/{claim_id}/failure", json={"reason": reason})


def start_run(configuration: dict[str, Any]) -> dict[str, Any]:
    return _call("POST", "/runs", json=configuration)


def get_run(run_id: str) -> dict[str, Any]:
    return _call("GET", f"/runs/{run_id}")


def get_run_threads(run_id: str) -> list[str]:
    return _call("GET", f"/runs/{run_id}/threads")


def record_run_selection(run_id: str, claim_ids: list[str]) -> dict[str, str]:
    return _call("PUT", f"/runs/{run_id}/selection", json={"claim_ids": claim_ids})


def record_run_claim(
    run_id: str, claim_id: str, status: str, error: str | None
) -> dict[str, Any]:
    return _call(
        "PUT",
        f"/runs/{run_id}/claims/{claim_id}",
        json={"status": status, "error": error},
    )


def finish_run(run_id: str) -> dict[str, Any]:
    return _call("POST", f"/runs/{run_id}/finish")


def stop_runs() -> list[str]:
    return _call("POST", "/runs/stop")


def health() -> dict[str, str]:
    return _call("GET", "/health")


def record_model_call(claim_id: str, measurement: dict[str, Any]) -> dict[str, str]:
    return _call("POST", f"/claims/{claim_id}/model-calls", json=measurement)


def record_work_slice(
    claim_id: str, slice_id: str, run_id: str | None, seconds: float
) -> dict[str, str]:
    return _call(
        "PUT",
        f"/claims/{claim_id}/work-slices/{slice_id}",
        json={"run_id": run_id, "seconds": seconds},
    )
