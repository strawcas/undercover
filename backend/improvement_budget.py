"""Round spending and conservative evaluation admission, without orchestration."""

from fastapi import HTTPException
from sqlalchemy import Connection, func, literal, select, union_all, update

from backend.database import (
    claims,
    evaluation_grades,
    evaluation_sources,
    model_calls,
    round_model_calls,
    rounds,
    run_claims,
    runs,
)
from backend.improvement_models import Quote, Spending


def spending(db: Connection, round_id: str, limit: float) -> Spending:
    """Sum immutable billing facts once, including replies received after stop."""
    billing = union_all(
        select(
            literal("improver").label("kind"), round_model_calls.c.cost.label("cost")
        ).where(round_model_calls.c.round_id == round_id),
        select(literal("claims").label("kind"), model_calls.c.cost.label("cost"))
        .join(runs, runs.c.run_id == model_calls.c.run_id)
        .where(runs.c.round_id == round_id),
        select(
            literal("judge").label("kind"),
            evaluation_grades.c.record["judge_cost"].as_float().label("cost"),
        )
        .join(runs, runs.c.run_id == evaluation_grades.c.run_id)
        .where(runs.c.round_id == round_id),
    ).subquery()
    rows = db.execute(
        select(
            billing.c.kind,
            func.sum(billing.c.cost).label("total"),
            func.count().label("replies"),
            func.count(billing.c.cost).label("known"),
        ).group_by(billing.c.kind)
    ).mappings()
    amounts = {"improver": 0.0, "claims": 0.0, "judge": 0.0}
    unknown = False
    for value in rows:
        amounts[value["kind"]] = value["total"] or 0.0
        unknown = unknown or value["known"] != value["replies"]
    total = sum(amounts.values())
    return Spending(
        improver=amounts["improver"],
        claims=amounts["claims"],
        judge=amounts["judge"],
        total=total,
        remaining=max(0, limit - total),
        unknown=unknown,
    )


def lock_round(db: Connection, round_id: str, *, shared: bool = False):
    """Protect parent state; compatible readers still exclude billing and stop."""
    if not shared and db.dialect.name == "sqlite":
        db.execute(
            update(rounds)
            .where(rounds.c.round_id == round_id)
            .values(status=rounds.c.status)
        )
    row = (
        db.execute(
            select(rounds)
            .where(rounds.c.round_id == round_id)
            .with_for_update(read=shared)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Round not found")
    return row


def admit(db: Connection, round_id: str, *, shared: bool = True) -> None:
    """Refuse new work after stop or exhaustion, including an unknown charge."""
    row = lock_round(db, round_id, shared=shared)
    if row["status"] in ("stopped", "finished"):
        raise HTTPException(409, f"Round is {row['status']}")
    cost = spending(db, round_id, row["record"]["settings"]["spending_limit"])
    if cost.unknown or cost.remaining <= 0:
        raise HTTPException(409, "Round spending limit reached or usage unavailable")


def admit_run(db: Connection, run_id: str | None) -> None:
    """Use the common run-then-round lock order before a claim or retry starts."""
    if run_id is None:
        return
    row = db.execute(
        select(runs.c.status, runs.c.round_id)
        .where(runs.c.run_id == run_id)
        .with_for_update(read=True)
    ).first()
    if row is None or row.status != "running":
        raise HTTPException(409, "Run is no longer running")
    if row.round_id is not None:
        admit(db, row.round_id)


def claim_run_lock(claim_id: str, run_id: str, *, sqlite: bool):
    """Lock the originating run before taking the parent or claim lock."""
    evaluation = select(claims.c.evaluation_id).where(claims.c.claim_id == claim_id)
    target = runs.c.run_id == func.coalesce(evaluation.scalar_subquery(), run_id)
    columns = (runs.c.run_id, runs.c.round_id, runs.c.status, runs.c.kind)
    if sqlite:
        return (
            update(runs).where(target).values(status=runs.c.status).returning(*columns)
        )
    return select(*columns).where(target).with_for_update(read=True, of=runs)


def guard_claim_write(db: Connection, claim_id: str, run_id: str = "") -> None:
    """Refuse late claim state writes after stop, before taking a claim row lock."""
    run = db.execute(
        claim_run_lock(claim_id, run_id, sqlite=db.dialect.name == "sqlite")
    ).first()
    if run_id and (run is None or run.run_id != run_id):
        raise HTTPException(409, "Claim work must use its own run")
    if run is not None and run.status != "running":
        name = "Evaluation run" if run.kind == "evaluation" else "Run"
        raise HTTPException(409, f"{name} is no longer running")
    if run is not None and run.round_id is not None:
        parent = lock_round(db, run.round_id, shared=True)
        if parent["status"] in ("stopped", "finished"):
            raise HTTPException(409, f"Round is {parent['status']}")


def observations(db: Connection) -> dict[str, float]:
    """Find the highest known model cost per selected claim across recorded runs."""
    call_costs = (
        select(
            model_calls.c.run_id,
            func.sum(model_calls.c.cost).label("cost"),
            func.count().label("calls"),
            func.count(model_calls.c.cost).label("known"),
        )
        .group_by(model_calls.c.run_id)
        .subquery()
    )
    members = (
        select(run_claims.c.run_id, func.count().label("count"))
        .group_by(run_claims.c.run_id)
        .subquery()
    )
    rows = db.execute(
        select(runs.c.model, call_costs.c.cost, members.c.count)
        .join(call_costs, runs.c.run_id == call_costs.c.run_id)
        .join(members, runs.c.run_id == members.c.run_id)
        .where(call_costs.c.calls == call_costs.c.known, runs.c.status == "finished")
    ).all()
    result: dict[str, float] = {}
    for model, cost, count in rows:
        result[model] = max(result.get(model, 0), cost / count)
    return result


def judge_observation(db: Connection) -> float:
    """Include the highest known judge charge per selected claim in the quote."""
    members = (
        select(run_claims.c.run_id, func.count().label("count"))
        .group_by(run_claims.c.run_id)
        .subquery()
    )
    rows = db.execute(
        select(
            evaluation_grades.c.run_id, evaluation_grades.c.record, members.c.count
        ).join(members, members.c.run_id == evaluation_grades.c.run_id)
    )
    totals: dict[str, tuple[float, int, bool]] = {}
    for identity, record, count in rows:
        total, _, known = totals.get(identity, (0, count, True))
        charge = record["judge_cost"]
        totals[identity] = (total + (charge or 0), count, known and charge is not None)
    return max(
        [0.001, *(total / count for total, count, known in totals.values() if known)]
    )


def quote(db: Connection, round_id: str, model: str, set_name: str) -> Quote:
    """Estimate model-specific cost, scaling unseen models by the larger price ratio."""
    row = lock_round(db, round_id)
    record = row["record"]
    cost = spending(db, round_id, record["settings"]["spending_limit"])
    models = record["models_allowed"]
    observed = observations(db)
    target = models.get(model)
    estimate = None
    if target is not None:
        unit = observed.get(model)
        if unit is None:
            base_model = record["base_model"]
            base = models[base_model]
            if target["input_price"] == target["output_price"] == 0:
                unit = 0
            elif (
                base_model in observed
                and base["input_price"] > 0
                and base["output_price"] > 0
            ):
                unit = observed[base_model] * max(
                    target["input_price"] / base["input_price"],
                    target["output_price"] / base["output_price"],
                )
            elif base_model not in observed:
                # Cold start: 50k input and 10k output tokens per claim.
                unit = (
                    50_000 * target["input_price"] + 10_000 * target["output_price"]
                ) / 1_000_000
        if unit is not None:
            count = db.execute(
                select(func.count())
                .select_from(evaluation_sources)
                .where(evaluation_sources.c.set_name == set_name)
            ).scalar_one()
            judge_unit = (
                0 if record["improver_effort"] == "default" else judge_observation(db)
            )
            estimate = count * (unit + judge_unit)
    allowed = (
        row["status"] not in ("finished", "stopped")
        and not cost.unknown
        and estimate is not None
        and estimate <= cost.remaining
        and cost.remaining > 0
    )
    reason = (
        "Within remaining budget"
        if allowed
        else (
            f"Evaluation refused: estimated ${estimate:.4f}, "
            f"remaining ${cost.remaining:.4f}"
        )
        if estimate is not None
        else "Evaluation refused: no reliable price estimate"
    )
    return Quote(
        estimate=estimate, remaining=cost.remaining, allowed=allowed, reason=reason
    )
