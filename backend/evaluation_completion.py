"""Complete evaluation grading without holding database connections during calls."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial

from fastapi import HTTPException
from sqlalchemy import Connection, select, update

from backend import evaluations, improvement_budget, judge
from backend import runs as run_records
from backend import settings as platform_settings
from backend.database import (
    claims,
    decisions,
    engine,
    evaluation_grades,
    insert_once,
    payments,
    runs,
)
from backend.judge_models import Grade, undecided
from backend.models import Comparison, Run


def grade_claim(item: Comparison, disabled: bool) -> tuple[str, Grade]:
    """Grade a decided claim, or supply the required no-call result."""
    if disabled:
        result = Grade(judge_cost=0)
    elif item.decision is None:
        result = undecided()
    else:
        result = judge.grade(
            {
                "handler": {
                    "decision": item.handler_decision,
                    "reason": item.handler_reason,
                    "reference": item.handler_reference,
                },
                "agent": {
                    "decision": item.decision,
                    "rationale": item.reason,
                    "section": item.agent_section,
                },
            }
        )
    return item.claim_id, result


@dataclass
class PreparedEvaluation:
    run: Run
    items: list[Comparison]


def prepare(run_id: str) -> PreparedEvaluation | Run:
    """Reserve evaluation grading or return an already closed dispatch/run."""
    with engine().begin() as db:
        row = run_records.run_row(db, run_id, lock=True)
        if row["kind"] != "evaluation":
            return run_records.finish_run(db, run_id)
        if row["status"] != "running":
            return run_records.get_run(db, run_id)
        if row["round_id"] is not None:
            try:
                improvement_budget.admit(db, row["round_id"])
            except HTTPException as error:
                return run_records.stop(db, run_id, str(error.detail))
        claimed = db.execute(
            update(runs)
            .where(
                runs.c.run_id == run_id,
                runs.c.status == "running",
                runs.c.grading_started.is_(False),
            )
            .values(grading_started=True)
            .returning(runs.c.run_id)
        ).scalar_one_or_none()
        if claimed is None:
            raise HTTPException(409, "This evaluation is already being graded")
        run = run_records.get_run(db, run_id)
        items = evaluations.comparisons(db, run)
    return PreparedEvaluation(run, items)


def hold_blocked_payments(db: Connection, run_id: str) -> None:
    """Keep unpaid evaluation approvals complete if a lower limit blocked payment."""
    limit = platform_settings.approval_limit(db)
    blocked = select(decisions.c.claim_id).where(
        decisions.c.decision == "approve",
        decisions.c.payout > limit,
        ~select(payments.c.claim_id)
        .where(payments.c.claim_id == decisions.c.claim_id)
        .exists(),
    )
    db.execute(
        update(claims)
        .where(
            claims.c.evaluation_id == run_id,
            claims.c.status == "in_progress",
            claims.c.claim_id.in_(blocked),
        )
        .values(status="waiting_for_sign_off")
    )


def save_results(run_id: str, results: list[tuple[str, Grade]]) -> Run:
    """Persist grading facts and the final score snapshot in one transaction."""
    with engine().begin() as db:
        row = run_records.run_row(db, run_id, lock=True)
        stop_reason = None
        if row.status == "running" and row.round_id is not None:
            try:
                improvement_budget.admit(db, row.round_id, shared=False)
            except HTTPException as error:
                stop_reason = str(error.detail)
        records = [
            {"run_id": run_id, "claim_id": claim_id, "record": judgement.model_dump()}
            for claim_id, judgement in results
        ]
        if records:
            insert_once(db, evaluation_grades, records)
        if row.status != "running":
            return run_records.get_run(db, run_id)
        if stop_reason:
            return run_records.stop(db, run_id, stop_reason)
        hold_blocked_payments(db, run_id)
        finished = run_records.finish_run(db, run_id)
        evaluation = evaluations.get_evaluation(db, run_id)
        db.execute(
            update(runs)
            .where(runs.c.run_id == run_id)
            .values(scores=evaluation.scores.model_dump())
        )
        return finished


def finish_run(run_id: str) -> Run:
    """Grade an evaluation in parallel, then freeze its outcomes once."""
    ready = prepare(run_id)
    if isinstance(ready, Run):
        return ready
    with ThreadPoolExecutor(max_workers=25) as workers:
        results = list(
            workers.map(
                partial(grade_claim, disabled=ready.run.reasoning_effort == "default"),
                ready.items,
            )
        )
    return save_results(run_id, results)
