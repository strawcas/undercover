"""The lab's scoring formulas, independent of storage and agent execution."""

from decimal import Decimal
from typing import Any

WRONGFUL_DECLINE = 1500
NEEDLESS_REFERRAL = 50
PAYOUT_TOLERANCE = Decimal("1")


def compare(
    decision: str | None, payout: float | None, expected: dict[str, Any]
) -> dict[str, Any]:
    """Score a missing answer as a referral, retaining its actual state elsewhere."""
    actual = decision or "refer"
    amount = Decimal(str(payout or 0))
    difference = abs(amount - Decimal(str(expected["payout"])))
    right_decision = actual == expected["decision"]
    if right_decision:
        cost = float(difference) if actual == "approve" else 0
    elif actual == "decline":
        cost = WRONGFUL_DECLINE
    elif actual == "approve":
        cost = float(amount)
    else:
        cost = NEEDLESS_REFERRAL
    return {
        "right_decision": right_decision,
        "right": right_decision and difference <= PAYOUT_TOLERANCE,
        "mistake_cost": cost,
    }
