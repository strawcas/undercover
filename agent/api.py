"""The UnderCover backend's API: one function for each operation the agent uses."""

import os
from enum import StrEnum
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

BACKEND_URL = os.getenv("UNDERCOVER_API", "http://127.0.0.1:8000")

backend = httpx.Client(base_url=f"{BACKEND_URL}/api", timeout=30)

DecisionKind = Literal["approve", "decline", "refer"]
APPROVE: DecisionKind = "approve"
DocumentName = Literal["policy_wording", "claims_manual"]
StepKind = Literal["tool_call", "tool_result", "message", "reading", "error"]
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


class Refused(Exception):
    """The backend refused a request, with its status code and reason."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code, self.detail = status_code, detail


def _call(method: str, path: str, **request: Any) -> Any:
    """Send one request to the backend and return its JSON, or raise Refused."""
    response = backend.request(method, path, **request)
    if response.is_error:
        raise Refused(response.status_code, response.json()["detail"])
    return response.json()


def list_claims() -> list[dict[str, Any]]:
    return _call("GET", "/claims")


def get_claim(claim_id: str) -> dict[str, Any]:
    return _call("GET", f"/claims/{claim_id}")


def get_policy(policy_id: str) -> dict[str, Any]:
    return _call("GET", f"/policies/{policy_id}")


def get_claim_history(customer_id: str) -> list[dict[str, Any]]:
    return _call("GET", f"/customers/{customer_id}/claims")


def get_instructions() -> dict[str, str]:
    return _call("GET", "/instructions")


def search_documents(query: str) -> list[dict[str, Any]]:
    return _call("GET", "/documents/search", params={"query": query})


def read_section(document: DocumentName, number: int) -> dict[str, Any]:
    return _call("GET", f"/documents/{document}/sections/{number}")


def calculate_payout(
    claim_id: str, benefit: Benefit, lines: list[Line], paid_by_others: float
) -> dict[str, Any]:
    body = {
        "benefit": benefit,
        "lines": [line.model_dump() for line in lines],
        "paid_by_others": paid_by_others,
    }
    return _call("POST", f"/claims/{claim_id}/payout", json=body)


def start_claim(claim_id: str, thread_id: str) -> dict[str, Any]:
    return _call("POST", f"/claims/{claim_id}/start", json={"thread_id": thread_id})


def record_step(claim_id: str, kind: StepKind, content: str) -> dict[str, Any]:
    body = {"kind": kind, "content": content}
    return _call("POST", f"/claims/{claim_id}/steps", json=body)


def record_decision(
    claim_id: str, decision: DecisionKind, payout: float, section: str, reason: str
) -> dict[str, Any]:
    body = {
        "decision": decision,
        "payout": payout,
        "section": section,
        "reason": reason,
    }
    return _call("POST", f"/claims/{claim_id}/decision", json=body)


def pay_claim(claim_id: str, amount: float) -> dict[str, Any]:
    return _call("POST", f"/claims/{claim_id}/payment", json={"amount": amount})


def fail_claim(claim_id: str, reason: str) -> dict[str, Any]:
    return _call("POST", f"/claims/{claim_id}/failure", json={"reason": reason})


def start_run(configuration: dict[str, Any]) -> dict[str, Any]:
    return _call("POST", "/runs", json=configuration)


def get_latest_run() -> dict[str, Any] | None:
    return _call("GET", "/runs/latest")


def get_run(run_id: str) -> dict[str, Any]:
    return _call("GET", f"/runs/{run_id}")


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
