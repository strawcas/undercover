"""Run records and measurements of the work performed in them."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    Connection,
    RowMapping,
    bindparam,
    case,
    func,
    insert,
    literal,
    select,
    update,
)

from backend.clock import now
from backend.configurations import get_configuration
from backend.database import (
    claim_view,
    claims,
    conflict_insert,
    insert_once,
    model_calls,
    run_claims,
    runs,
    steps,
    work_slices,
)
from backend.improvement_budget import lock_round
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


def measured_work(db: Connection, run_id: str) -> dict[str, dict[str, Any]]:
    """Aggregate costs and active seconds in two queries regardless of swarm size."""
    work: dict[str, dict[str, Any]] = {}
    calls = select(
        model_calls.c.claim_id,
        func.count().label("received"),
        func.count(model_calls.c.cost).label("known"),
        func.sum(model_calls.c.cost).label("cost"),
    )
    for row in db.execute(
        calls.where(model_calls.c.run_id == run_id).group_by(model_calls.c.claim_id)
    ).mappings():
        work[row["claim_id"]] = {
            "llm_cost": row["cost"] if row["known"] == row["received"] else None,
            "active_seconds": 0.0,
        }
    slices = select(
        work_slices.c.claim_id,
        func.sum(work_slices.c.seconds).label("seconds"),
        func.min(work_slices.c.started_at).label("started_at"),
    )
    for row in db.execute(
        slices.where(work_slices.c.run_id == run_id).group_by(work_slices.c.claim_id)
    ).mappings():
        work.setdefault(row["claim_id"], {"llm_cost": 0.0}).update(
            active_seconds=row["seconds"], started_at=row["started_at"]
        )
    return work


def claim_results(db: Connection, claim_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Read actual outcomes in one query without claim payloads or expected labels."""
    query = claim_view.with_only_columns(
        claims.c.claim_id,
        claim_view.selected_columns.customer_name,
        claims.c.status.label("claim_status"),
        claim_view.selected_columns.decision,
        claim_view.selected_columns.payout,
        claim_view.selected_columns.reason,
        claim_view.selected_columns.decided_at,
        claim_view.selected_columns.paid_at,
    ).where(claims.c.claim_id.in_(claim_ids))
    return {row["claim_id"]: dict(row) for row in db.execute(query).mappings()}


def member_result(db: Connection, claim_id: str) -> dict[str, Any]:
    """Read a single outcome or refuse an unknown claim."""
    result = claim_results(db, [claim_id]).get(claim_id)
    if result is None:
        raise HTTPException(404, f"Claim {claim_id} not found")
    return result


def preserve_hold(db: Connection, claim: RowMapping) -> None:
    """Save a dispatch's held outcome before a handler changes the live claim."""
    claim_id = claim["claim_id"]
    worked_claim = (
        select(work_slices.c.slice_id)
        .where(
            work_slices.c.run_id == runs.c.run_id,
            work_slices.c.claim_id == claim_id,
            work_slices.c.thread_id == claim["thread_id"],
        )
        .exists()
    )
    assigned_run = (
        (runs.c.run_id == claim["review_run_id"])
        if claim["returned_decisions"]
        else worked_claim
    )
    dispatches = (
        select(runs.c.run_id)
        .where(runs.c.status == "running", runs.c.kind == "dispatch", assigned_run)
        .with_for_update()
    )
    saved = member_result(db, claim_id) | {"held_for_review": True}
    saved["claim_status"] = "waiting_for_sign_off"
    db.execute(
        update(run_claims)
        .where(
            run_claims.c.claim_id == claim_id,
            run_claims.c.run_id.in_(dispatches),
            run_claims.c.result["held_for_review"].as_boolean().is_not(True),
        )
        .values(result=saved)
    )


def members(
    db: Connection, row: Any, work: dict[str, dict[str, Any]]
) -> list[RunMember]:
    """Read current outcomes in bulk or use frozen results for a closed run."""
    query = (
        select(run_claims)
        .where(run_claims.c.run_id == row["run_id"])
        .order_by(run_claims.c.position)
    )
    records = list(db.execute(query).mappings())
    refresh = [
        member["claim_id"]
        for member in records
        if row["status"] == "running" or "elapsed_seconds" not in member["result"]
    ]
    live = claim_results(db, refresh) if refresh else {}
    items = []
    for member in records:
        claim_id = member["claim_id"]
        measured = work.get(claim_id, {"llm_cost": 0.0, "active_seconds": 0.0})
        result = live.get(claim_id, {})
        if row["status"] != "running" or (member["result"] or {}).get(
            "held_for_review"
        ):
            result = result | member["result"]
        if claim_id in refresh:
            result = result | member_timing(row, result, measured.get("started_at"))
        items.append(
            RunMember(
                **dict(member)
                | result
                | {key: measured[key] for key in ("llm_cost", "active_seconds")}
            )
        )
    return items


def member_timing(
    run: Any, result: dict[str, Any], started_at: str | None
) -> dict[str, Any]:
    """Use only work and completed outcomes within this dispatch's interval."""
    lower = datetime.fromisoformat(run["started_at"])
    upper = datetime.fromisoformat(run["finished_at"] or now())
    started = datetime.fromisoformat(started_at) if started_at else None
    if started is not None and not lower <= started <= upper:
        started_at, started = None, None
    finished_at = (
        result["paid_at"] if result["decision"] == "approve" else result["decided_at"]
    )
    finished = datetime.fromisoformat(finished_at) if finished_at else None
    if result["claim_status"] != "decided" or (
        finished is not None and not (started or lower) <= finished <= upper
    ):
        finished_at, finished = None, None
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": (finished - started).total_seconds()
        if started is not None and finished is not None
        else None,
    }


def get_run(db: Connection, run_id: str) -> Run:
    """Build a run's contract with a fixed query count as the swarm grows."""
    row = run_row(db, run_id)
    work = measured_work(db, run_id)
    costs = [item["llm_cost"] for item in work.values()]
    seconds = (
        datetime.fromisoformat(row["finished_at"] or now())
        - datetime.fromisoformat(row["started_at"])
    ).total_seconds()
    return Run(
        **dict(row),
        members=members(db, row, work),
        seconds=max(0, seconds),
        llm_cost=None if None in costs else sum(costs),
        active_seconds=sum(item["active_seconds"] for item in work.values()),
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


def active(db: Connection) -> Run | None:
    """Read any running kind so both dashboard forms share admission state."""
    run_id = db.execute(
        select(runs.c.run_id)
        .where(runs.c.status == "running")
        .order_by(runs.c.number.desc())
        .limit(1)
    ).scalar_one_or_none()
    return get_run(db, run_id) if run_id else None


def start_run(db: Connection, request: RunRequest) -> Run:
    """Persist the service's accepted configuration."""
    run_id = str(uuid.uuid4())
    configuration = get_configuration(db, request.configuration_version)
    values = request.model_dump() | {
        "model": configuration.model,
        "reasoning_effort": configuration.reasoning_effort,
    }
    db.execute(
        insert(runs).values(run_id=run_id, status="running", started_at=now(), **values)
    )
    return get_run(db, run_id)


def record_member(
    db: Connection, run_id: str, claim_id: str, request: RunClaimRequest
) -> None:
    """Keep one membership record through retries and execution updates."""
    if request.status == "selected":
        select_members(db, run_id, [claim_id])
        return
    row = running(db, run_id)
    result = member_result(db, claim_id)
    claim = (
        db.execute(select(claims).where(claims.c.claim_id == claim_id)).mappings().one()
    )
    validate_scope(row, claim["evaluation_id"])
    key = (run_claims.c.run_id == run_id) & (run_claims.c.claim_id == claim_id)
    outcome = case(
        (
            run_claims.c.result["held_for_review"].as_boolean().is_(True),
            run_claims.c.result,
        ),
        else_=literal(result, type_=JSON),
    )
    updated = db.execute(
        update(run_claims).where(key).values(**request.model_dump(), result=outcome)
    )
    if updated.rowcount != 1:
        raise HTTPException(409, "Claim was not selected for this run")


def validate_scope(run: Any, evaluation_id: str | None) -> None:
    """Keep dispatches and evaluations within their own claim population."""
    if (run["kind"] == "dispatch" and evaluation_id is not None) or (
        run["kind"] == "evaluation" and evaluation_id != run["run_id"]
    ):
        raise HTTPException(409, "Claim does not belong to this run's selection")


def select_members(db: Connection, run_id: str, claim_ids: list[str]) -> None:
    """Register ordered membership with a fixed number of database round trips."""
    run = running(db, run_id)
    ids = list(dict.fromkeys(claim_ids))
    selected = list(
        db.execute(
            select(claims.c.claim_id, claims.c.evaluation_id).where(
                claims.c.claim_id.in_(ids)
            )
        ).mappings()
    )
    if len(selected) != len(ids):
        raise HTTPException(404, "One or more selected claims do not exist")
    for claim in selected:
        validate_scope(run, claim["evaluation_id"])
    existing = set(
        db.execute(
            select(run_claims.c.claim_id).where(run_claims.c.run_id == run_id)
        ).scalars()
    )
    new_ids = [claim_id for claim_id in ids if claim_id not in existing]
    records = [
        {
            "run_id": run_id,
            "claim_id": claim_id,
            "position": len(existing) + index,
            "status": "selected",
        }
        for index, claim_id in enumerate(new_ids)
    ]
    if records:
        insert_once(db, run_claims, records)


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
    noun = "claim" if len(items) == 1 else "claims"
    lines = [f"Worked {len(items)} {noun}: {', '.join(counts)}."]
    for item in items:
        if item.claim_status == "waiting_for_sign_off":
            lines.append(f"{item.claim_id}: ${item.payout:,.2f} waiting for sign-off.")
        elif item.claim_status in ("left_for_handler", "in_progress", "waiting"):
            lines.append(f"{item.claim_id}: {item.claim_status.replace('_', ' ')}.")
    return "\n".join(lines)


def finish_run(db: Connection, run_id: str, status: str = "finished") -> Run:
    """Freeze every selected outcome and build the final summary."""
    row = dict(running(db, run_id)) | {"finished_at": now()}
    snapshots = members(db, row, measured_work(db, run_id))
    if snapshots:
        db.execute(
            update(run_claims)
            .where(
                run_claims.c.run_id == bindparam("frozen_run"),
                run_claims.c.claim_id == bindparam("frozen_claim"),
            )
            .values(result=bindparam("frozen_result")),
            [
                {
                    "frozen_run": run_id,
                    "frozen_claim": member.claim_id,
                    "frozen_result": member.model_dump(),
                }
                for member in snapshots
            ],
        )
    db.execute(
        update(runs)
        .where(runs.c.run_id == run_id)
        .values(
            status=status, finished_at=row["finished_at"], summary=summary(snapshots)
        )
    )
    return get_run(db, run_id)


def stop_runs(db: Connection) -> list[str]:
    """Close abandoned runs; ordinary claim threads remain resumable."""
    ids = list(
        db.execute(select(runs.c.run_id).where(runs.c.status == "running")).scalars()
    )
    for run_id in ids:
        finish_run(db, run_id, "stopped")
    return evaluation_threads(db, None)


def evaluation_threads(db: Connection, run_id: str | None) -> list[str]:
    """Include every attempt, even an old failed thread or a pending cleanup."""
    selection = (
        claims.c.evaluation_id == run_id
        if run_id
        else claims.c.evaluation_id.is_not(None)
    )
    current = select(claims.c.thread_id).where(
        selection, claims.c.thread_id.is_not(None)
    )
    slices = (
        select(work_slices.c.thread_id)
        .join(claims, claims.c.claim_id == work_slices.c.claim_id)
        .where(selection)
    )
    history = (
        select(steps.c.thread_id)
        .join(claims, claims.c.claim_id == steps.c.claim_id)
        .where(selection)
    )
    return list(db.execute(current.union(slices, history)).scalars())


def has_work_slice(db: Connection, run_id: str, claim_attempt: tuple[str, str]) -> bool:
    """Verify that this dispatch had already started work on the same attempt."""
    claim_id, thread_id = claim_attempt
    query = select(work_slices.c.slice_id).where(
        work_slices.c.run_id == run_id,
        work_slices.c.claim_id == claim_id,
        work_slices.c.thread_id == thread_id,
    )
    return db.execute(query.limit(1)).first() is not None


def validate_run(
    db: Connection, run_id: str | None, claim_attempt: tuple[str, str] | None = None
) -> str | None:
    """Validate live work or a model receipt recovered from a stopped dispatch."""
    if run_id is None:
        return None
    row = db.execute(
        select(runs.c.status, runs.c.kind, runs.c.round_id)
        .where(runs.c.run_id == run_id)
        .with_for_update(read=True)
    ).first()
    if row is None:
        raise HTTPException(404, "Run not found")
    if row.status != "running" and not (
        row.status == "stopped"
        and row.kind == "dispatch"
        and claim_attempt is not None
        and has_work_slice(db, run_id, claim_attempt)
    ):
        raise HTTPException(409, "Run has finished")
    return row.round_id


def record_model_call(
    db: Connection, claim_id: str, thread_id: str, request: ModelCallRequest
) -> None:
    """Save each provider reply once, preserving unavailable measurements."""
    round_id = validate_run(db, request.run_id, (claim_id, thread_id))
    if round_id is not None:
        lock_round(db, round_id)
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
    query = (
        conflict_insert(db, work_slices)
        .values(
            {
                "claim_id": claim_id,
                "thread_id": thread_id,
                "slice_id": slice_id,
                "started_at": now(),
                "updated_at": now(),
                **request.model_dump(),
            }
        )
        .on_conflict_do_update(
            index_elements=[work_slices.c.slice_id],
            set_={"seconds": request.seconds, "updated_at": now()},
            where=(work_slices.c.claim_id == claim_id)
            & (work_slices.c.thread_id == thread_id)
            & work_slices.c.run_id.is_not_distinct_from(request.run_id)
            & (work_slices.c.seconds < request.seconds),
        )
        .returning(work_slices.c.slice_id)
    )
    if db.execute(query).first() is not None:
        return
    row = db.execute(select(work_slices).where(key)).mappings().one()
    if (row["claim_id"], row["thread_id"], row["run_id"]) != (
        claim_id,
        thread_id,
        request.run_id,
    ):
        raise HTTPException(409, "Work slice belongs to another execution")
