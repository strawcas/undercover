"""Pristine evaluation inputs, isolated copies and backend-only comparisons."""

import uuid

from fastapi import HTTPException
from sqlalchemy import Connection, insert, select

from backend import runs as run_records
from backend.database import (
    claims,
    evaluation_labels,
    evaluation_sources,
    run_claims,
    runs,
)
from backend.models import (
    Comparison,
    Evaluation,
    EvaluationDetail,
    Run,
    RunRequest,
    Scores,
)
from backend.scoring import compare


def create_copies(db: Connection, run_id: str, request: RunRequest) -> None:
    """Create selected sources as new working claims in the run's transaction."""
    query = (
        select(evaluation_sources.c.source_id, evaluation_sources.c.record)
        .join(
            evaluation_labels,
            (evaluation_sources.c.set_name == evaluation_labels.c.set_name)
            & (evaluation_sources.c.source_id == evaluation_labels.c.source_id),
        )
        .where(evaluation_sources.c.set_name == request.set_name)
        .order_by(evaluation_sources.c.source_id)
    )
    if request.tiny:
        query = query.where(evaluation_labels.c.tiny.is_(True))
    for position, source in enumerate(db.execute(query).mappings()):
        claim_id = f"EVAL-{uuid.uuid4()}"
        record = dict(source["record"]) | {"claim_id": claim_id}
        db.execute(
            insert(claims).values(
                claim_id=claim_id,
                customer_id=record["customer_id"],
                record=record,
                status="waiting",
                attempts=0,
                evaluation_id=run_id,
                source_id=source["source_id"],
            )
        )
        db.execute(
            insert(run_claims).values(
                run_id=run_id, claim_id=claim_id, position=position, status="selected"
            )
        )


def comparisons(db: Connection, run: Run) -> list[Comparison]:
    """Join expected labels only in this dashboard-facing backend operation."""
    if run.kind != "evaluation":
        raise HTTPException(404, "Evaluation not found")
    rows = db.execute(
        select(claims.c.claim_id, claims.c.source_id, evaluation_labels.c.record)
        .join(
            evaluation_labels,
            (evaluation_labels.c.source_id == claims.c.source_id)
            & (evaluation_labels.c.set_name == run.set_name),
        )
        .where(claims.c.evaluation_id == run.run_id)
    ).mappings()
    labels = {row["claim_id"]: row for row in rows}
    result = []
    for member in run.members:
        source = labels[member.claim_id]
        expected = source["record"]
        result.append(
            Comparison(
                **member.model_dump(),
                source_id=source["source_id"],
                handler_decision=expected["decision"],
                handler_payout=expected["payout"],
                **compare(member.decision, member.payout, expected),
            )
        )
    return result


def get_evaluation(db: Connection, run_id: str) -> EvaluationDetail:
    """Score every selected claim, including unstarted and stopped copies."""
    run = run_records.get_run(db, run_id)
    items = comparisons(db, run)
    count = len(items)
    denominator = count or 1
    scores = Scores(
        decision_accuracy=sum(item.right_decision for item in items) / denominator,
        accuracy=sum(item.right for item in items) / denominator,
        cost_of_mistakes=sum(item.mistake_cost for item in items),
        straight_through=sum(
            item.decision is not None and item.decision != "refer" for item in items
        )
        / denominator,
        model_cost_per_claim=None
        if run.llm_cost is None
        else run.llm_cost / denominator,
        seconds_per_claim=run.active_seconds / denominator,
    )
    return EvaluationDetail(**run.model_dump(), scores=scores, comparisons=items)


def list_evaluations(db: Connection) -> list[Evaluation]:
    """Return newest-first evaluations with all measures and no claim inputs."""
    ids = db.execute(
        select(runs.c.run_id)
        .where(runs.c.kind == "evaluation")
        .order_by(runs.c.number.desc())
    ).scalars()
    return [Evaluation(**get_evaluation(db, run_id).model_dump()) for run_id in ids]
