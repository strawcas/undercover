"""Serialize new work across the laptop, Lambda and Runtime sessions."""

from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy import Connection, select, update

from backend import agent_service, improvements
from backend import runs as run_records
from backend.clock import has_live_lease, now
from backend.database import engine, rounds, runs, work_gate
from backend.improvements import WORKING_ROUND

STOPPED_ANSWERING = "The agent stopped answering"


def lock(db: Connection) -> None:
    """Take the seeded admission row with a write on both supported databases."""
    result = db.execute(
        update(work_gate).where(work_gate.c.id == 1).values(id=work_gate.c.id)
    )
    if result.rowcount != 1:
        raise RuntimeError("The work gate is missing from the database")


def stop_round(
    db: Connection,
    round_id: str,
    reason: str,
    *,
    work_id: str | None = None,
    stale_at: str | None = None,
) -> None:
    """Stop a round and its evaluation in the existing run-then-round lock order."""
    nested = (
        select(runs.c.run_id)
        .where(runs.c.round_id == round_id, runs.c.status == "running")
        .with_for_update()
    )
    children = list(db.execute(nested).scalars())
    value = improvements.row(db, round_id)
    record = value["record"]
    if value["status"] in ("finished", "stopped"):
        return
    if work_id is not None and (
        record.get("work_id") != work_id or value["status"] not in WORKING_ROUND
    ):
        raise HTTPException(409, "The round is on a different stretch")
    if stale_at and (
        value["status"] not in WORKING_ROUND
        or improvements.live_lease(record, stale_at)
    ):
        return
    for run_id in children:
        run_records.stop(db, run_id, reason)
    improvements.finish(db, round_id, True, reason)


def cancel_round_start(db: Connection, round_id: str, reason: str) -> None:
    """Compensate a definite rejection unless the new round has an owner."""
    record = db.execute(
        select(rounds.c.record).where(rounds.c.round_id == round_id)
    ).scalar_one()
    if not record.get("heartbeat_at"):
        stop_round(db, round_id, reason)


def admit(db: Connection) -> None:
    """Recover abandoned work and check the slot while the caller creates work."""
    lock(db)
    at = now()
    active_runs = (
        select(runs)
        .where(runs.c.status == "running", runs.c.round_id.is_(None))
        .with_for_update()
    )
    for run in db.execute(active_runs).mappings().all():
        if has_live_lease(run["started_at"], run["heartbeat_at"], at):
            owner = "a dispatch" if run["kind"] == "dispatch" else "an evaluation"
            raise HTTPException(409, f"Cannot start while {owner} is running")
        run_records.stop(db, run["run_id"], STOPPED_ANSWERING)
    active_rounds = select(rounds).where(rounds.c.status.in_(WORKING_ROUND))
    for current in db.execute(active_rounds).mappings().all():
        if improvements.live_lease(current["record"], at):
            raise HTTPException(
                409, "Cannot start while an improvement round is running"
            )
        stop_round(db, current["round_id"], STOPPED_ANSWERING, stale_at=at)


def take_run(db: Connection, run_id: str) -> None:
    """Hand one running standalone run to the worker that accepts it."""
    lock(db)
    at = now()
    run = run_records.run_row(db, run_id)
    if not has_live_lease(run["started_at"], run["heartbeat_at"], at):
        raise HTTPException(409, "The run's work lease expired")
    taken = db.execute(
        update(runs)
        .where(
            runs.c.run_id == run_id,
            runs.c.status == "running",
            runs.c.round_id.is_(None),
            runs.c.heartbeat_at.is_(None),
        )
        .values(heartbeat_at=at)
    )
    if taken.rowcount != 1:
        raise HTTPException(409, "This run is no longer available")


def admit_round_evaluation(db: Connection, round_id: str) -> None:
    """Accept one evaluation at a time under the worker's live round lease."""
    lock(db)
    child = db.execute(
        select(runs.c.run_id)
        .where(runs.c.round_id == round_id, runs.c.status == "running")
        .limit(1)
    ).first()
    if child is not None:
        raise HTTPException(409, "An evaluation is already running for this round")
    value = improvements.row(db, round_id, writable=True)
    record = value["record"]
    if (
        value["status"] not in WORKING_ROUND
        or not record.get("heartbeat_at")
        or not improvements.live_lease(record, now())
    ):
        raise HTTPException(409, "The round has no active worker")


def heartbeat_run(db: Connection, run_id: str) -> None:
    """Renew a running worker without letting stopped work appear live again."""
    lock(db)
    at = now()
    run = run_records.run_row(db, run_id)
    if not has_live_lease(run["started_at"], run["heartbeat_at"], at):
        raise HTTPException(409, "The run's work lease expired")
    changed = db.execute(
        update(runs)
        .where(
            runs.c.run_id == run_id,
            runs.c.status == "running",
            runs.c.heartbeat_at.is_not(None),
        )
        .values(heartbeat_at=at)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Run is no longer running")


def launch(
    operation: str,
    identities: dict[str, str],
    undo: Callable[[Connection, str], object],
) -> None:
    """Invoke outside the transaction and compensate a definite rejection."""
    try:
        agent_service.invoke(operation, **identities)
    except agent_service.InvocationError as error:
        if not error.uncertain:
            with engine().begin() as db:
                lock(db)
                undo(db, str(error.detail))
        raise
