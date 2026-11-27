"""Handler reads for customer lookup, run follow-up and payment coverage."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Connection, case, func, select

from backend import evaluations, runs, settings
from backend.auth import CurrentHandler
from backend.copilot_models import (
    Customer,
    CustomerMatch,
    Dispatch,
    HandoffClaim,
    PayoutCoverage,
)
from backend.database import (
    claim_view,
    claims,
    customers,
    decisions,
    payments,
    policies,
)
from backend.dependencies import Database
from backend.models import Evaluation

router = APIRouter()


@router.get("/api/customers", operation_id="search_customers")
def search_customers(
    name: Annotated[str, Query(min_length=1, max_length=100)],
    handler: CurrentHandler,
    db: Database,
) -> list[CustomerMatch]:
    """Search customer names without case sensitivity and return matching IDs."""
    query = (
        select(customers.c.customer_id, customers.c.name)
        .where(customers.c.name.icontains(name, autoescape=True))
        .order_by(customers.c.name, customers.c.customer_id)
    )
    return [CustomerMatch.model_validate(row) for row in db.execute(query).mappings()]


@router.get("/api/customers/{customer_id}", operation_id="get_customer")
def get_customer(customer_id: str, handler: CurrentHandler, db: Database) -> Customer:
    """Read one customer by ID with their policy and queue claim IDs."""
    record = db.scalar(
        select(customers.c.record).where(customers.c.customer_id == customer_id)
    )
    if record is None:
        raise HTTPException(404, f"Customer {customer_id} not found")
    policy_ids = db.scalars(
        select(policies.c.policy_id)
        .where(policies.c.record["customer_id"].as_string() == customer_id)
        .order_by(policies.c.policy_id)
    )
    claim_ids = db.scalars(
        select(claims.c.claim_id)
        .where(claims.c.customer_id == customer_id, claims.c.evaluation_id.is_(None))
        .order_by(claims.c.claim_id)
    )
    return Customer(**record, policy_ids=list(policy_ids), claim_ids=list(claim_ids))


def handoff(db: Connection) -> dict[str, list[HandoffClaim]]:
    """Use current claim decisions and returns for the dispatch handoff."""
    rows = db.execute(
        claim_view.where(claims.c.evaluation_id.is_(None)).order_by(claims.c.claim_id)
    ).mappings()
    held, referred, left, returned = [], [], [], []
    for row in rows:
        item = HandoffClaim(
            claim_id=row["claim_id"],
            customer_name=row["customer_name"],
            status=row["status"],
            reason=row["reason"],
        )
        if row["returned_decisions"] and row["decision"] is None:
            returned.append(
                item.model_copy(
                    update={"reason": row["returned_decisions"][-1]["note"]}
                )
            )
        elif item.status == "waiting_for_sign_off":
            held.append(item)
        elif row["decision"] == "refer":
            referred.append(item)
        elif item.status == "left_for_handler":
            left.append(item)
    return {
        "held": held,
        "referred": referred,
        "left_for_handler": left,
        "returned": returned,
    }


@router.get("/api/dispatches/{run_id}", operation_id="get_dispatch")
def get_dispatch(run_id: str, handler: CurrentHandler, db: Database) -> Dispatch:
    """Read one dispatch and the claims that currently need a handler."""
    run = runs.get_run(db, run_id)
    if run.kind != "dispatch":
        raise HTTPException(404, "Dispatch not found")
    return Dispatch.model_validate(run.model_dump() | handoff(db))


@router.get(
    "/api/evaluations/{run_id}/measures", operation_id="get_evaluation_measures"
)
def get_evaluation_measures(
    run_id: str, handler: CurrentHandler, db: Database
) -> Evaluation:
    """Read an evaluation's status and aggregate measures without handler labels."""
    if runs.run_row(db, run_id)["kind"] != "evaluation":
        raise HTTPException(404, "Evaluation not found")
    return Evaluation.model_validate(
        evaluations.get_evaluation(db, run_id).model_dump(exclude={"comparisons"})
    )


@router.get("/api/settings/payout-coverage", operation_id="get_payout_coverage")
def get_payout_coverage(handler: CurrentHandler, db: Database) -> PayoutCoverage:
    """Count original payments above the current limit and those with sign-off."""
    limit = settings.approval_limit(db)
    paid, signed = db.execute(
        select(
            func.count(), func.count(case((decisions.c.signed_off_by.is_not(None), 1)))
        )
        .select_from(
            payments.join(claims).outerjoin(
                decisions, decisions.c.claim_id == payments.c.claim_id
            )
        )
        .where(claims.c.evaluation_id.is_(None), payments.c.amount > limit)
    ).one()
    return PayoutCoverage(limit=limit, paid=paid, signed_off=signed)
