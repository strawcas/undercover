"""Run records and measurements of the work performed in them."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, func, insert, select, update

from backend.clock import now
from backend.database import (
    claim_view,
    claims,
    insert_once,
    model_calls,
    run_claims,
    runs,
    work_slices,
)
from backend.models import (
    ModelCallRequest,
    Run,
    RunClaimRequest,
    RunMember,
    RunRequest,
    WorkSliceRequest,
)


def run_row(db: Connection, run_id: str) -> Any:
    """Find a run or raise a 404."""
    row = db.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()
    if row is None:
        raise HTTPException(404, f"Run {run_id} not found")
    return row


def running(db: Connection, run_id: str) -> Any:
    """Lock a live run before changing its membership or lifecycle."""
    row = (
        db.execute(select(runs).where(runs.c.run_id == run_id).with_for_update())
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, f"Run {run_id} not found")
    if row["status"] != "running":
        raise HTTPException(409, f"Run {run_id} has finished")
    return row


def measurements(
    db: Connection, run_id: str, claim_id: str | None = None
) -> dict[str, Any]:
    """Sum only the work attributed to this run, including failed attempts."""
    calls = select(model_calls.c.cost).where(model_calls.c.run_id == run_id)
    slices = select(func.coalesce(func.sum(work_slices.c.seconds), 0)).where(
        work_slices.c.run_id == run_id
    )
    if claim_id is not None:
        calls = calls.where(model_calls.c.claim_id == claim_id)
        slices = slices.where(work_slices.c.claim_id == claim_id)
    costs = list(db.execute(calls).scalars())
    return {
        "llm_cost": None if None in costs else sum(costs),
        "active_seconds": db.execute(slices).scalar_one(),
    }


def member_result(db: Connection, claim_id: str) -> dict[str, Any]:
    """Read a claim's actual outcome without any expected evaluation label."""
    row = db.execute(claim_view.where(claims.c.claim_id == claim_id)).mappings().first()
    if row is None:
        raise HTTPException(404, f"Claim {claim_id} not found")
    return {
        "claim_status": row["status"],
        "decision": row["decision"],
        "payout": row["payout"],
        "reason": row["reason"],
    }


def members(db: Connection, row: Any) -> list[RunMember]:
    """Read current progress, or frozen results once the run finishes."""
    query = (
        select(run_claims)
        .where(run_claims.c.run_id == row["run_id"])
        .order_by(run_claims.c.position)
    )
    return [
        RunMember(
            **dict(member)
            | (
                member["result"]
                if row["status"] != "running"
                else member_result(db, member["claim_id"])
            )
            | measurements(db, row["run_id"], member["claim_id"])
        )
        for member in db.execute(query).mappings()
    ]


def get_run(db: Connection, run_id: str) -> Run:
    """Build a run's read contract from records and attributed measurements."""
    row = run_row(db, run_id)
    seconds = (
        datetime.fromisoformat(row["finished_at"] or now())
        - datetime.fromisoformat(row["started_at"])
    ).total_seconds()
    return Run(
        **dict(row),
        members=members(db, row),
        seconds=max(0, seconds),
        **measurements(db, run_id),
    )


def latest(db: Connection) -> Run | None:
    """Select the newest dispatch; evaluations belong on the scoreboard."""
    run_id = db.execute(
        select(runs.c.run_id)
        .where(runs.c.kind == "dispatch")
        .order_by(runs.c.number.desc())
        .limit(1)
    ).scalar_one_or_none()
    return get_run(db, run_id) if run_id else None


def start_run(db: Connection, request: RunRequest) -> Run:
    """Persist the service's accepted configuration."""
    run_id = str(uuid.uuid4())
    db.execute(
        insert(runs).values(
            run_id=run_id, status="running", started_at=now(), **request.model_dump()
        )
    )
    return get_run(db, run_id)


def record_member(
    db: Connection, run_id: str, claim_id: str, request: RunClaimRequest
) -> Run:
    """Keep one membership record through retries and execution updates."""
    row = running(db, run_id)
    result = member_result(db, claim_id)
    claim = (
        db.execute(select(claims).where(claims.c.claim_id == claim_id)).mappings().one()
    )
    if (row["kind"] == "dispatch" and claim["evaluation_id"] is not None) or (
        row["kind"] == "evaluation" and claim["evaluation_id"] != run_id
    ):
        raise HTTPException(409, "Claim does not belong to this run's selection")
    key = (run_claims.c.run_id == run_id) & (run_claims.c.claim_id == claim_id)
    if request.status == "selected":
        position = db.execute(
            select(func.count())
            .select_from(run_claims)
            .where(run_claims.c.run_id == run_id)
        ).scalar_one()
        insert_once(
            db,
            run_claims,
            {
                "run_id": run_id,
                "claim_id": claim_id,
                "position": position,
                **request.model_dump(),
            },
        )
    else:
        if db.execute(select(run_claims).where(key)).first() is None:
            raise HTTPException(409, "Claim was not selected for this run")
        db.execute(
            update(run_claims).where(key).values(**request.model_dump(), result=result)
        )
    return get_run(db, run_id)


def summary(items: list[RunMember]) -> str:
    """Describe decisions, held payouts and unresolved claims without a model."""
    counts = [
        f"{sum(m.decision == decision for m in items)} {label}"
        for decision, label in [
            ("approve", "approved"),
            ("decline", "declined"),
            ("refer", "referred"),
        ]
    ]
    lines = [f"Worked {len(items)} claims: {', '.join(counts)}."]
    for item in items:
        if item.claim_status == "waiting_for_sign_off":
            lines.append(f"{item.claim_id}: ${item.payout:,.2f} waiting for sign-off.")
        elif item.claim_status in ("left_for_handler", "in_progress", "waiting"):
            lines.append(f"{item.claim_id}: {item.claim_status.replace('_', ' ')}.")
    return "\n".join(lines)


def finish_run(db: Connection, run_id: str, status: str = "finished") -> Run:
    """Freeze every selected outcome and build the final summary."""
    running(db, run_id)
    ids = db.execute(
        select(run_claims.c.claim_id).where(run_claims.c.run_id == run_id)
    ).scalars()
    for claim_id in ids:
        db.execute(
            update(run_claims)
            .where(run_claims.c.run_id == run_id, run_claims.c.claim_id == claim_id)
            .values(result=member_result(db, claim_id))
        )
    run = get_run(db, run_id)
    db.execute(
        update(runs)
        .where(runs.c.run_id == run_id)
        .values(status=status, finished_at=now(), summary=summary(run.members))
    )
    return get_run(db, run_id)


def stop_runs(db: Connection) -> list[str]:
    """Close abandoned runs; ordinary claim threads remain resumable."""
    ids = list(
        db.execute(select(runs.c.run_id).where(runs.c.status == "running")).scalars()
    )
    for run_id in ids:
        finish_run(db, run_id, "stopped")
    return list(
        db.execute(
            select(claims.c.thread_id).where(
                claims.c.evaluation_id.in_(ids), claims.c.thread_id.is_not(None)
            )
        ).scalars()
    )


def validate_run(db: Connection, run_id: str | None) -> None:
    """Reject a measurement referring to an unknown run."""
    if run_id is not None:
        row = db.execute(
            select(runs.c.status)
            .where(runs.c.run_id == run_id)
            .with_for_update(read=True)
        ).first()
        if row is None:
            raise HTTPException(404, "Run not found")
        if row.status != "running":
            raise HTTPException(409, "Run has finished")


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
