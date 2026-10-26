"""Typed responses for existing claim and handler operations."""

from typing import Literal

from pydantic import BaseModel, JsonValue

from backend.harness_models import Outcome, PaymentStatus
from backend.models import Claim, State

ReviewReason = Literal["two_failed_assessments", "payment_failed"]


class QueueRow(BaseModel):
    claim_id: str
    claim_type: str
    summary: str
    amount_claimed: str
    status: State
    failed_attempts: int
    review_reason: ReviewReason | None
    customer_name: str
    outcome: Outcome | None
    payment_status: PaymentStatus
    run_active: bool


class Decision(BaseModel):
    thread_id: str
    outcome: Outcome
    payout: str
    calculation: str
    explanation: str
    policy_sections: list[str]
    manual_sections: list[str]
    policy_wording_version: str | None
    claims_manual_version: str | None
    created_at: str


class Payment(BaseModel):
    amount: str
    created_at: str
    simulated: Literal[True]


class SignOff(BaseModel):
    handler_id: str
    handler_name: str
    amount: str
    created_at: str


class ClaimDetail(Claim):
    thread_id: str | None
    run_active: bool
    run_id: str | None
    failed_attempts: int
    review_reason: ReviewReason | None
    policy: dict[str, JsonValue]
    customer: dict[str, JsonValue]
    plan: dict[str, JsonValue]
    add_ons: list[dict[str, JsonValue]]
    decision: Decision | None
    sign_off: SignOff | None
    payment: Payment | None


class ClaimEvent(BaseModel):
    id: int
    claim_id: str
    thread_id: str
    action: str
    status: Literal["started", "completed", "blocked", "failed"]
    detail: str
    created_at: str


class Wording(BaseModel):
    version: str
    text: str


class Health(BaseModel):
    status: Literal["ok"]


class Recorded(BaseModel):
    status: Literal["recorded"]
