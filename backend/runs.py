"""Run records and measurements of the work performed in them."""

from fastapi import HTTPException
from sqlalchemy import Connection, select, update

from backend.clock import now
from backend.database import insert_once, model_calls, runs, work_slices
from backend.models import ModelCallRequest, WorkSliceRequest


def validate_run(db: Connection, run_id: str | None) -> None:
    """Reject a measurement referring to an unknown run."""
    if (
        run_id is not None
        and db.execute(select(runs.c.run_id).where(runs.c.run_id == run_id)).first()
        is None
    ):
        raise HTTPException(404, "Run not found")


def record_model_call(
    db: Connection, claim_id: str, thread_id: str, request: ModelCallRequest
) -> None:
    """Save each provider reply once, preserving unavailable measurements."""
    validate_run(db, request.run_id)
    insert_once(
        db,
        model_calls,
        {"claim_id": claim_id, "thread_id": thread_id, **request.model_dump()},
    )


def record_work_slice(
    db: Connection,
    claim_id: str,
    thread_id: str,
    slice_id: str,
    request: WorkSliceRequest,
) -> None:
    """Save cumulative elapsed work without counting downtime or handler waits."""
    validate_run(db, request.run_id)
    key = work_slices.c.slice_id == slice_id
    insert_once(
        db,
        work_slices,
        {
            "claim_id": claim_id,
            "thread_id": thread_id,
            "slice_id": slice_id,
            "started_at": now(),
            "updated_at": now(),
            **request.model_dump(),
        },
    )
    row = db.execute(select(work_slices).where(key)).mappings().one()
    if (row["claim_id"], row["thread_id"], row["run_id"]) != (
        claim_id,
        thread_id,
        request.run_id,
    ):
        raise HTTPException(409, "Work slice belongs to another execution")
    db.execute(
        update(work_slices)
        .where(key, work_slices.c.seconds < request.seconds)
        .values(seconds=request.seconds, updated_at=now())
    )
