"""Pristine evaluation inputs, isolated copies and backend-only comparisons."""

import uuid
from statistics import mean

from fastapi import HTTPException
from sqlalchemy import Connection, insert, select

from backend import runs as run_records
from backend.database import (
    claims,
    decisions,
    evaluation_grades,
    evaluation_labels,
    evaluation_sources,
    run_claims,
    runs,
)
from backend.judge_models import Grade, undecided
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


def saved_grades(db: Connection, run_id: str) -> dict[str, Grade]:
    """Read saved judge results without making another model call."""
    rows = db.execute(
        select(evaluation_grades).where(evaluation_grades.c.run_id == run_id)
    ).mappings()
    return {row["claim_id"]: Grade(**row["record"]) for row in rows}


def comparisons(
    db: Connection, run: Run, grades: dict[str, Grade] | None = None
) -> list[Comparison]:
    """Join expected labels only in this dashboard-facing backend operation."""
    if run.kind != "evaluation":
        raise HTTPException(404, "Evaluation not found")
    rows = db.execute(
        select(
            claims.c.claim_id,
            claims.c.source_id,
            evaluation_labels.c.record,
            decisions.c.section,
        )
        .join(
            evaluation_labels,
            (evaluation_labels.c.source_id == claims.c.source_id)
            & (evaluation_labels.c.set_name == run.set_name),
        )
        .outerjoin(decisions, decisions.c.claim_id == claims.c.claim_id)
        .where(claims.c.evaluation_id == run.run_id)
    ).mappings()
    labels = {row["claim_id"]: row for row in rows}
    grades = saved_grades(db, run.run_id) if grades is None else grades
    result = []
    for member in run.members:
        source = labels[member.claim_id]
        expected = source["record"]
        graded = grades.get(member.claim_id, Grade(judge_cost=0))
        if (
            member.decision is None
            and run.status != "running"
            and run.reasoning_effort != "default"
        ):
            graded = undecided()
        result.append(
            Comparison(
                **member.model_dump(),
                source_id=source["source_id"],
                handler_decision=expected["decision"],
                handler_payout=expected["payout"],
                handler_reason=expected.get("reason", ""),
                handler_reference=expected.get("reference", ""),
                agent_section=source["section"],
                **graded.model_dump(exclude={"judge_cost"}),
                **compare(member.decision, member.payout, expected),
            )
        )
    return result


def get_evaluation(db: Connection, run_id: str) -> EvaluationDetail:
    """Score every selected claim, including unstarted and stopped copies."""
    run = run_records.get_run(db, run_id)
    grades = saved_grades(db, run_id)
    items = comparisons(db, run, grades)
    graded = [item.grade for item in items if item.grade is not None]
    costs = [value.judge_cost for value in grades.values()]
    judge_cost = (
        None if None in costs else sum(value for value in costs if value is not None)
    )
    count = len(items)
    denominator = count or 1
    scores = Scores(
        grade=mean(graded) if graded else None,
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
    return EvaluationDetail(
        **run.model_dump(), scores=scores, comparisons=items, judge_cost=judge_cost
    )


def list_evaluations(db: Connection) -> list[Evaluation]:
    """Return newest-first evaluations with all measures and no claim inputs."""
    ids = db.execute(
        select(runs.c.run_id)
        .where(runs.c.kind == "evaluation")
        .order_by(runs.c.number.desc())
    ).scalars()
    return [Evaluation(**get_evaluation(db, run_id).model_dump()) for run_id in ids]


def version_evaluations(db: Connection, version: int, set_name: str) -> list[Scores]:
    """Read frozen whole-set round scores, excluding manual and stopped runs."""
    values = db.execute(
        select(runs.c.scores).where(
            runs.c.configuration_version == version,
            runs.c.kind == "evaluation",
            runs.c.set_name == set_name,
            runs.c.tiny.is_(False),
            runs.c.round_id.is_not(None),
            runs.c.status == "finished",
            runs.c.scores.is_not(None),
        )
    ).scalars()
    return [Scores(**value) for value in values]


def development_examples(db: Connection, version: int) -> list[dict]:
    """Read mistakes and the five lowest graded correct development explanations."""
    identity = (
        db.execute(
            select(runs.c.run_id)
            .where(
                runs.c.configuration_version == version,
                runs.c.set_name == "evaluation",
                runs.c.status == "finished",
                runs.c.round_id.is_not(None),
                runs.c.tiny.is_(False),
            )
            .order_by(runs.c.number.desc())
        )
        .scalars()
        .first()
    )
    if identity is None:
        return []
    items = get_evaluation(db, identity).comparisons
    weakest = sorted(
        (item for item in items if item.right and item.grade is not None),
        key=lambda item: item.grade if item.grade is not None else 3,
    )[:5]
    chosen = [item for item in items if not item.right] + weakest
    sources = db.execute(
        select(evaluation_sources.c.source_id, evaluation_sources.c.record).where(
            evaluation_sources.c.set_name == "evaluation",
            evaluation_sources.c.source_id.in_([item.source_id for item in chosen]),
        )
    )
    summaries = {source_id: record["summary"] for source_id, record in sources}
    return [
        item.model_dump() | {"summary": summaries[item.source_id]} for item in chosen
    ]


def version_scores(db: Connection, version: int, set_name: str) -> Scores | None:
    """Average eligible run measures as in the lab, preserving unavailable cost."""
    values = version_evaluations(db, version, set_name)
    if not values:
        return None
    result = {}
    for name in Scores.model_fields:
        measurements = [getattr(value, name) for value in values]
        if name == "grade":
            measurements = [value for value in measurements if value is not None]
        result[name] = (
            mean(measurements) if measurements and None not in measurements else None
        )
    return Scores(**result)
