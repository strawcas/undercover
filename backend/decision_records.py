"""Evidence needed to complete a claim's decision."""

from fastapi import HTTPException
from sqlalchemy import RowMapping

from backend.models import SECTION_14, DecisionRequest


def require_attempt(claim: RowMapping, thread_id: str) -> None:
    """Reject a stale tool request before it can affect a different attempt."""
    if claim["thread_id"] != thread_id:
        raise HTTPException(409, "The claim is on a different attempt")


def calculation_for(claim: RowMapping, request: DecisionRequest) -> dict | None:
    """Return the current attempt's matching calculation for an approval."""
    if request.decision != "approve":
        return None
    calculation = claim["latest_calculation"]
    if calculation is None:
        raise HTTPException(
            422, f"{SECTION_14}: An approval needs a calculation for this attempt"
        )
    payout = calculation["payout"]
    if round(request.payout, 2) != payout:
        raise HTTPException(
            422,
            f"{SECTION_14}: The payout must equal the calculated payout "
            f"of ${payout:,.2f}",
        )
    return calculation
