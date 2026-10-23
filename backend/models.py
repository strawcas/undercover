"""Shared records and exact USD amounts."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
State = Literal["waiting", "in_progress", "awaiting_sign_off", "decided"]


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
