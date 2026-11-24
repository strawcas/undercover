"""Senior reviews and temporary ownership of returned claim work."""

from fastapi import HTTPException
from sqlalchemy import Connection, RowMapping, delete, update

from backend.clock import has_recent_heartbeat, now
from backend.database import claim_reviews, claims, conflict_insert, decisions
from backend.models import ClaimReview, Decision, ReturnedDecision


def review_active(claim: RowMapping, at: str) -> bool:
    """Identify a claim whose review worker still holds it."""
    return (
        bool(claim["review_owner"])
        and (claim["review_run_id"] is None or claim["review_run_status"] == "running")
        and has_recent_heartbeat(claim["review_heartbeat"], at)
    )


def record_return(db: Connection, claim: RowMapping, review: ClaimReview) -> None:
    """Archive the locked approval and clear the live decision and calculation."""
    claim_id = claim["claim_id"]
    decision = ReturnedDecision(
        **review.model_dump(),
        decision=Decision(**{key: claim[key] for key in Decision.model_fields}),
        approval_limit=claim["approval_limit"],
    )
    values = {
        "history": [*(claim["returned_decisions"] or []), decision.model_dump()],
        "revision": (claim["revision"] or 0) + 1,
        "owner": None,
        "run_id": None,
        "heartbeat_at": None,
    }
    db.execute(
        conflict_insert(db, claim_reviews)
        .values(claim_id=claim_id, **values)
        .on_conflict_do_update(index_elements=[claim_reviews.c.claim_id], set_=values)
    )
    db.execute(delete(decisions).where(decisions.c.claim_id == claim_id))
    db.execute(
        update(claims)
        .where(claims.c.claim_id == claim_id)
        .values(
            status="sent_back",
            latest_calculation=None,
            memory_pending=False,
            attempts=0,
        )
    )


def take(db: Connection, claim: RowMapping, owner: str, run_id: str | None) -> None:
    """Assign an eligible locked claim to one worker and fence earlier writers."""
    at = now()
    claim_id = claim["claim_id"]
    if (
        not claim["returned_decisions"]
        or claim["status"] not in {"sent_back", "in_progress", "waiting"}
        or review_active(claim, at)
    ):
        raise HTTPException(
            409, f"Claim {claim_id} is no longer available for review work"
        )
    db.execute(
        update(claim_reviews)
        .where(claim_reviews.c.claim_id == claim_id)
        .values(
            revision=claim["revision"] + 1, owner=owner, run_id=run_id, heartbeat_at=at
        )
    )
    if claim["status"] == "sent_back":
        db.execute(
            update(claims)
            .where(claims.c.claim_id == claim_id)
            .values(status="in_progress", attempts=1)
        )


def update_heartbeat(
    db: Connection, claim: RowMapping, owner: str, working: bool
) -> None:
    """Renew or release only the current worker's claim."""
    at = now()
    if claim["review_owner"] != owner or (working and not review_active(claim, at)):
        raise HTTPException(
            409, f"Claim {claim['claim_id']} has a different review worker"
        )
    db.execute(
        update(claim_reviews)
        .where(claim_reviews.c.claim_id == claim["claim_id"])
        .values(owner=owner if working else None, heartbeat_at=at if working else None)
    )
