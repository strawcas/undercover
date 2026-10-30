"""Exact payout calculations from UnderCover's stored benefit rules."""

from decimal import Decimal
from math import ceil
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models import Money
from backend.storage import Store

Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]
MAXIMUM = {
    benefit: f"{benefit}_max"
    for benefit in (
        "trip_cancellation",
        "trip_delay",
        "baggage_delay",
        "baggage",
        "passport",
        "emergency_medical",
    )
} | {"trip_interruption": "trip_cancellation_max"}


class PayoutLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1)
    amount: Money
    quantity: int = Field(ge=1)
    kind: Literal[
        "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
    ]
    nights: int = Field(ge=0)
    hours: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def duration(self) -> Self:
        if self.kind == "hotel" and not self.nights:
            raise ValueError("Hotel expenses need a positive number of nights.")
        if self.kind == "meals_without_receipts" and not self.hours:
            raise ValueError("Meals without receipts need a positive delay duration.")
        return self


class PayoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    benefit: Benefit
    lines: list[PayoutLine] = Field(min_length=1)
    paid_by_others: Money


class CalculationStep(BaseModel):
    step: str
    amount: str


class Payout(BaseModel):
    calculation: list[CalculationStep]
    payout: str
    needs_sign_off: bool


def calculate(store: Store, claim_id: str, request: PayoutInput) -> Payout:
    """Calculate covered lines without deciding or paying a claim."""
    claim = store.record("claim", claim_id)
    policy = store.record("policy", claim["policy_id"])
    rules = store.record("settings", "plans")
    plan = next(p for p in rules["plans"] if p["plan"] == policy["plan"])
    electronics = next(a for a in rules["add_ons"] if a["add_on"] == "electronics_plus")
    calculation: list[CalculationStep] = []
    travelers = len(policy["travelers"])
    for line in request.lines:
        if line.kind == "cash":
            continue
        limit = line_limit(line, plan, policy["add_ons"], electronics, travelers)
        amount = min(line.amount, limit)
        description = line.description
        if amount < line.amount:
            description += f", limited to ${amount:,.2f}"
        calculation.append(CalculationStep(step=description, amount=f"{amount:.2f}"))
    cash = sum(
        (line.amount for line in request.lines if line.kind == "cash"), Decimal(0)
    )
    if cash:
        calculation.append(
            CalculationStep(
                step="Cash, up to $100 in all", amount=f"{min(cash, Decimal(100)):.2f}"
            )
        )
    total = sum((Decimal(s.amount) for s in calculation), Decimal(0))
    total = subtract(
        calculation,
        total,
        request.paid_by_others,
        "Less what others have paid or refunded",
    )
    if request.benefit in rules["deductible_applies_to"] and total > 0:
        total = subtract(
            calculation,
            total,
            min(total, Decimal(plan["deductible"])),
            "Less the deductible",
        )
    maximum = Decimal(plan[MAXIMUM[request.benefit]])
    if request.benefit in {"trip_cancellation", "trip_interruption"}:
        maximum = min(maximum, Decimal(policy["trip"]["trip_cost"]))
    if total > maximum:
        total = subtract(
            calculation,
            total,
            total - maximum,
            f"Less the amount over the maximum of ${maximum:,.0f}",
        )
    if total < 0:
        total = subtract(calculation, total, total, "Nothing is left to pay")
    limit = Decimal(store.record("settings", "company")["payout_approval_limit"])
    return Payout(
        calculation=calculation, payout=f"{total:.2f}", needs_sign_off=total > limit
    )


def subtract(
    steps: list[CalculationStep], total: Decimal, amount: Decimal, description: str
) -> Decimal:
    if amount:
        steps.append(CalculationStep(step=description, amount=f"{-amount:.2f}"))
    return total - amount


def line_limit(
    line: PayoutLine, plan: dict, add_ons: list[str], electronics: dict, travelers: int
) -> Decimal:
    match line.kind:
        case "item":
            return Decimal(plan["per_item_limit"]) * line.quantity
        case "electronics":
            maximum = (
                electronics["electronics_per_item_limit"]
                if "electronics_plus" in add_ons
                else plan["per_item_limit"]
            )
            return Decimal(maximum) * line.quantity
        case "hotel":
            return Decimal(350) * ceil(travelers / 2) * line.nights
        case "meals_without_receipts":
            return Decimal(35) * travelers * ceil(line.hours / 24)
        case _:
            return line.amount
