"""Shared records and exact USD amounts."""

import re
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
State = Literal[
    "waiting", "in_progress", "awaiting_sign_off", "decided", "handler_review"
]


def cents(amount: Decimal) -> int:
    """Convert a validated dollar amount to cents."""
    return int(amount * 100)


def dollars(amount: int) -> str:
    """Render stored cents as exact dollars."""
    return f"{Decimal(amount) / 100:.2f}"


class Item(BaseModel):
    description: str
    category: str
    amount: Money


class Document(BaseModel):
    name: str
    type: str
    content: str


class Claim(BaseModel):
    claim_id: str
    policy_id: str
    customer_id: str
    claim_type: str
    submitted_on: str
    incident_date: str
    incident_location: str
    summary: str
    story: str
    items: list[Item]
    amount_claimed: Money
    documents: list[Document]
    status: State


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="allow")


class Policy(SourceRecord):
    policy_id: str
    customer_id: str
    plan: str
    add_ons: list[str]


class Customer(SourceRecord):
    customer_id: str
    name: str


class Handler(BaseModel):
    handler_id: str
    name: str
    role: str
    email: str
    can_approve_over_limit: bool


class Company(BaseModel):
    name: str
    currency: Literal["USD"]
    payout_approval_limit: Money
    policy_wording_version: str


class DecisionInput(BaseModel):
    thread_id: str = Field(min_length=1)
    outcome: Literal["approve", "decline", "refer"]
    payout: Money
    calculation: str = Field(min_length=1)
    explanation: str = Field(min_length=20)
    policy_sections: list[Annotated[str, Field(pattern=r"^(?:[1-9]|1[0-2])$")]] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def valid_decision(self) -> Self:
        if (self.outcome == "approve") != (self.payout > 0):
            raise ValueError(
                "Approvals need a positive payout; other outcomes need zero."
            )
        if len(re.split(r"(?<=[.!?])\s+", self.explanation.strip())) not in {2, 3}:
            raise ValueError("Explain the decision in two or three sentences.")
        return self


class PaymentInput(BaseModel):
    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class SignOffInput(BaseModel):
    handler_id: str = Field(min_length=1)


class EventInput(BaseModel):
    thread_id: str = Field(min_length=1)
    action: str = Field(min_length=1, max_length=100)
    status: Literal["started", "completed", "blocked", "failed"]
    detail: str = Field(min_length=1, max_length=2000)
