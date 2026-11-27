"""The UnderCover backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from mangum import Mangum
from sqlalchemy import Connection, RowMapping, insert, or_, select, update

from backend import (
    agent_service,
    claim_reviews,
    configurations,
    copilot_records,
    evaluation_completion,
    evaluations,
    improvement_budget,
    improvements,
    work_admission,
)
from backend import runs as run_records
from backend import settings as platform_settings
from backend.agent_contract import agent_contract
from backend.auth import (
    CurrentHandler,
    ReviewHandler,
    SeniorHandler,
    VerifiedEmail,
    agent_or_handler,
    handler_for_email,
    require_agent,
)
from backend.clock import now
from backend.configuration_models import ClaimConfiguration, ConfigurationHistory
from backend.copilot_models import ActionReceipt
from backend.copilot_reads import router as copilot_reads_router
from backend.copilot_routes import router as copilot_router
from backend.dashboard import install_dashboard
from backend.database import (
    claim_view,
    claims,
    customers,
    decisions,
    engine,
    handlers,
    is_seeded,
    payments,
    policies,
    running_on_lambda,
    seed,
    settings,
    steps,
    upgrade,
)
from backend.decision_records import calculation_for, require_attempt, require_revision
from backend.dependencies import Database
from backend.documents import manual_version
from backend.improvement_models import EvaluationQuote
from backend.improvement_routes import router as improvement_router
from backend.models import (
    Claim,
    ClaimHeartbeat,
    ClaimModelCall,
    ClaimPayoutRequest,
    ClaimReview,
    ClaimSummary,
    Decision,
    DecisionAttemptRequest,
    DecisionRequest,
    DispatchRequest,
    Evaluation,
    EvaluationDetail,
    EvaluationRequest,
    FailureRequest,
    FinishMemoryRequest,
    GatewayHoldRequest,
    Handler,
    LimitHoldRequest,
    MemorySave,
    NewStepRequest,
    Payment,
    PaymentRequest,
    Payout,
    Plans,
    Policy,
    PreviousClaim,
    Refusal,
    Run,
    RunAdmission,
    RunClaimRequest,
    RunKind,
    RunRequest,
    RunSelectionRequest,
    SecurityStepRequest,
    SendBackRequest,
    SetName,
    Status,
    Step,
    StepContent,
    StoppedWork,
    TakeClaimRequest,
    WorkSliceRequest,
)
from backend.payout import needs_sign_off, work_out_payout
from backend.settings_models import PlatformSettings, SettingsView

ATTEMPTS = 2


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Seed the database on startup if it has no tables yet."""
    if not running_on_lambda():
        if is_seeded(engine()):
            upgrade(engine())
        else:
            seed(engine())
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)
app.include_router(improvement_router)
app.include_router(copilot_router)
app.include_router(copilot_reads_router)
agent_only = [Depends(require_agent)]
shared_read = [Depends(agent_or_handler)]


@app.post("/api/dispatches", operation_id="start_dispatch", status_code=202)
def start_dispatch(
    request: DispatchRequest, email: VerifiedEmail, http: Request
) -> dict[str, str]:
    """Admit and commit one dispatch before asking the agent to work it."""
    return launch_run(
        "dispatch",
        request.concurrency,
        None,
        None,
        RunRequester(email, copilot_receipt(http)),
    )


@app.post("/api/evaluations", operation_id="start_evaluation", status_code=202)
def start_evaluation(
    request: EvaluationRequest, email: VerifiedEmail, http: Request
) -> dict[str, str]:
    """Admit an evaluation and its copies before asking the agent to work it."""
    return launch_run(
        "evaluation",
        request.concurrency,
        request.set_name,
        request.tiny,
        RunRequester(email, copilot_receipt(http)),
    )


@dataclass(frozen=True)
class RunRequester:
    email: str
    receipt: ActionReceipt | None


def copilot_receipt(http: Request) -> ActionReceipt | None:
    """Resolve the optional lease supplied by the Copilot's action client."""
    raw = http.headers.get("X-UnderCover-Copilot-Receipt")
    if raw is None:
        return None
    try:
        return ActionReceipt.from_header(raw)
    except ValueError as error:
        raise HTTPException(422, "Invalid Copilot action receipt") from error


def launch_run(
    kind: RunKind,
    concurrency: int,
    set_name: SetName | None,
    tiny: bool | None,
    requester: RunRequester,
) -> dict[str, str]:
    """Pin and commit backend-owned work before making any remote call."""
    receipt = requester.receipt
    with engine().begin() as db:
        handler = handler_for_email(requester.email, db)
        if receipt:
            copilot_records.take_action(db, handler.handler_id, receipt)
            if existing := copilot_records.saved_action(db, receipt):
                return {"status": "accepted", "run_id": existing}
        work_admission.admit(db)
        run = create_run(
            db,
            RunRequest(
                kind=kind,
                concurrency=concurrency,
                configuration_version=configurations.current_version(db),
                round_id=None,
                set_name=set_name,
                tiny=tiny,
            ),
        )
        if receipt:
            copilot_records.save_action(db, receipt, run.run_id)
    try:
        work_admission.launch(
            "work_run",
            {"run_id": run.run_id},
            lambda db, reason: run_records.stop(
                db, run.run_id, reason, only_unclaimed=True
            ),
        )
    except agent_service.InvocationError as error:
        if receipt is None or not error.uncertain:
            raise
    return {"status": "accepted", "run_id": run.run_id}


@app.get("/api/me", operation_id="get_current_handler")
def get_current_handler(handler: CurrentHandler) -> Handler:
    """Return the signed-in handler and their permissions."""
    return handler


@app.get("/api/evaluations", operation_id="list_evaluations")
def list_evaluations(handler: CurrentHandler, db: Database) -> list[Evaluation]:
    """List newest-first evaluations with backend-calculated scores."""
    return evaluations.list_evaluations(db)


@app.get("/api/evaluations/{run_id}", operation_id="get_evaluation")
def get_evaluation(
    run_id: str, handler: CurrentHandler, db: Database
) -> EvaluationDetail:
    """Compare the agent's results with handler labels on isolated copies."""
    return evaluations.get_evaluation(db, run_id)


def dollars(amount: float) -> str:
    """Format an amount like $1,250.00."""
    return f"${amount:,.2f}"


def plans(db: Connection) -> Plans:
    """Return the plans and add-ons, and the benefits the deductible applies to."""
    return Plans(**db.execute(select(settings.c.plans)).scalar_one())


def find_claim(
    db: Connection, claim_id: str, lock: bool = False, *, run_id: str = ""
) -> RowMapping:
    """Return one claim's row, or raise a 404."""
    query = claim_view.where(claims.c.claim_id == claim_id)
    if lock:
        improvement_budget.guard_claim_write(db, claim_id, run_id)
        if db.dialect.name == "sqlite":
            db.execute(
                update(claims)
                .where(claims.c.claim_id == claim_id)
                .values(thread_id=claims.c.thread_id)
            )
        else:
            db.execute(
                select(claims.c.claim_id)
                .where(claims.c.claim_id == claim_id)
                .with_for_update()
            ).first()
    row = db.execute(query).mappings().first()
    if row is None:
        raise HTTPException(404, f"Claim {claim_id} not found")
    return row


def find_policy(db: Connection, policy_id: str) -> Policy:
    """Return one policy, or raise a 404."""
    query = select(policies.c.record).where(policies.c.policy_id == policy_id)
    record = db.execute(query).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, f"Policy {policy_id} not found")
    return Policy(**record)


def set_status(db: Connection, claim_id: str, status: Status) -> None:
    """Move a claim to a new status."""
    db.execute(
        update(claims).where(claims.c.claim_id == claim_id).values(status=status)
    )


def to_summary(row: RowMapping, at: str) -> ClaimSummary:
    """Build a claim's line in the queue from its row."""
    return ClaimSummary(
        **row["record"]
        | {
            "status": row["status"],
            "customer_name": row["customer_name"],
            "decision": row["decision"],
            "payout": row["payout"],
            "review_active": claim_reviews.review_active(row, at),
        }
    )


def to_claim(row: RowMapping) -> Claim:
    """Build a claim in full, with its decision and payment, from its row."""
    decision = None
    if row["decision"] is not None:
        decision = Decision(**{field: row[field] for field in Decision.model_fields})
    payment = None
    if row["paid_at"] is not None:
        payment = Payment(**{field: row[field] for field in Payment.model_fields})
    return Claim(
        **row["record"]
        | {
            "status": row["status"],
            "customer_name": row["customer_name"],
            "thread_id": row["thread_id"],
            "attempts": row["attempts"],
            "decision": decision,
            "payment": payment,
            "sign_off_available": can_settle_sign_off(row, row["current_limit"]),
            "evaluation_id": row["evaluation_id"],
            "source_id": row["source_id"],
            "returned_decisions": row["returned_decisions"] or [],
            "revision": row["revision"] or 0,
            "review_active": claim_reviews.review_active(row, now()),
        }
    )


def add_step(
    db: Connection, claim_id: str, thread_id: str, kind: str, content: StepContent
) -> Step:
    """Add one step of the agent's work to a claim."""
    query = (
        insert(steps)
        .values(
            claim_id=claim_id,
            thread_id=thread_id,
            kind=kind,
            content=content
            if isinstance(content, str)
            else content.model_dump(mode="json"),
            created_at=now(),
        )
        .returning(steps)
    )
    return Step(**db.execute(query).mappings().one())


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get(
    "/api/agent/openapi.json",
    operation_id="get_agent_openapi",
    dependencies=shared_read,
)
def get_agent_openapi() -> dict[str, Any]:
    """Describe the claim agent's app operations for the Gateway."""
    return agent_contract(app)


@app.get("/api/claims", operation_id="list_claims", dependencies=shared_read)
def list_claims(db: Database) -> list[ClaimSummary]:
    """List every claim in the queue, in id order."""
    rows = db.execute(
        claim_view.where(claims.c.evaluation_id.is_(None)).order_by(claims.c.claim_id)
    ).mappings()
    at = now()
    return [to_summary(row, at) for row in rows]


@app.get("/api/claims/{claim_id}", operation_id="get_claim", dependencies=shared_read)
def get_claim(claim_id: str, db: Database) -> Claim:
    """Get one claim in full: the story, the items claimed and the documents."""
    return to_claim(find_claim(db, claim_id))


@app.get(
    "/api/policies/{policy_id}", operation_id="get_policy", dependencies=shared_read
)
def get_policy(policy_id: str, db: Database) -> Policy:
    """Get a policy: the plan, add-ons, purchase date, trip and travelers."""
    return find_policy(db, policy_id)


@app.get(
    "/api/customers/{customer_id}/claims",
    operation_id="get_claim_history",
    dependencies=shared_read,
)
def get_claim_history(customer_id: str, db: Database) -> list[PreviousClaim]:
    """Get a customer's earlier claims, with the outcome and the amount paid."""
    query = select(customers.c.record).where(customers.c.customer_id == customer_id)
    record = db.execute(query).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, f"Customer {customer_id} not found")
    return [PreviousClaim(**claim) for claim in record["previous_claims"]]


@app.get(
    "/api/configurations/current",
    operation_id="get_current_configuration",
    dependencies=shared_read,
)
def get_current_configuration(db: Database) -> ClaimConfiguration:
    """Get the current claim configuration and document contents without scores."""
    return configurations.get_configuration(db, configurations.current_version(db))


@app.get("/api/configurations", operation_id="list_configurations")
def list_configurations(
    handler: CurrentHandler, db: Database
) -> list[ConfigurationHistory]:
    """List configuration versions and their changes newest first."""
    return [
        value.model_copy(
            update={
                "held_out_scores": evaluations.version_scores(
                    db, value.version, "held_out"
                )
            }
        )
        for value in configurations.history(db)
    ]


@app.get(
    "/api/configurations/{version}",
    operation_id="get_configuration",
    dependencies=agent_only,
)
def get_configuration(version: int, db: Database) -> ClaimConfiguration:
    """Get an immutable claim configuration and prompt without scores."""
    return configurations.get_configuration(db, version)


@app.get("/api/settings", operation_id="get_settings")
def get_settings(handler: CurrentHandler, db: Database) -> SettingsView:
    """Show the business settings, allowed models and change history."""
    return platform_settings.view(db)


@app.put("/api/settings", operation_id="change_settings")
def change_settings(
    request: PlatformSettings, handler: SeniorHandler, db: Database
) -> SettingsView:
    """Change platform settings with the signed-in senior handler's audit trail."""
    return platform_settings.change_settings(db, request, handler)


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers(handler: CurrentHandler, db: Database) -> list[Handler]:
    """List the claims handlers."""
    rows = db.execute(select(handlers).order_by(handlers.c.handler_id)).mappings()
    return [Handler(**row) for row in rows]


@app.post(
    "/api/claims/{claim_id}/start", operation_id="start_claim", dependencies=agent_only
)
def start_claim(claim_id: str, request: DecisionAttemptRequest, db: Database) -> Claim:
    """Start work on a waiting claim, which moves it to in progress."""
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id)
    improvement_budget.admit_run(db, request.run_id or claim["evaluation_id"])
    require_revision(claim, request.revision)
    if claim["status"] != "waiting":
        raise HTTPException(409, f"Claim {claim_id} is not waiting")
    db.execute(
        update(claims)
        .where(claims.c.claim_id == claim_id)
        .values(
            status="in_progress",
            thread_id=request.thread_id,
            latest_calculation=None,
            memory_pending=False,
            attempts=claims.c.attempts + 1,
        )
    )
    return to_claim(find_claim(db, claim_id))


def original_for_memory(
    db: Connection, claim_id: str, request: DecisionAttemptRequest
) -> RowMapping:
    """Lock the original claim and verify the memory receipt's attempt."""
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id)
    require_attempt(claim, request.thread_id)
    require_revision(claim, request.revision)
    if claim["evaluation_id"] is not None:
        raise HTTPException(409, "Customer memory is available for original claims")
    return claim


@app.get(
    "/api/agent/pending-memory",
    operation_id="list_pending_memory",
    dependencies=agent_only,
)
def list_pending_memory(db: Database) -> list[str]:
    """List recorded originals whose memory events still need saving."""
    query = (
        select(claims.c.claim_id)
        .join(decisions, decisions.c.claim_id == claims.c.claim_id)
        .outerjoin(payments, payments.c.claim_id == claims.c.claim_id)
        .where(
            claims.c.memory_pending.is_(True),
            claims.c.evaluation_id.is_(None),
            claims.c.status == "decided",
            or_(decisions.c.decision != "approve", payments.c.paid_at.is_not(None)),
        )
        .order_by(decisions.c.decided_at, claims.c.claim_id)
    )
    return list(db.execute(query).scalars())


@app.post(
    "/api/claims/{claim_id}/prepare-memory",
    operation_id="prepare_memory",
    dependencies=agent_only,
)
def prepare_memory(
    claim_id: str, request: DecisionAttemptRequest, db: Database
) -> dict[str, str]:
    """Mark an original attempt to save memory after recording its decision."""
    claim = original_for_memory(db, claim_id, request)
    if claim["status"] != "decided" or (
        claim["decision"] == "approve" and claim["paid_at"] is None
    ):
        raise HTTPException(409, "The claim has no settled decision")
    db.execute(
        update(claims).where(claims.c.claim_id == claim_id).values(memory_pending=True)
    )
    return {"status": "ready"}


@app.post(
    "/api/claims/{claim_id}/finish-memory",
    operation_id="finish_memory",
    dependencies=agent_only,
)
def finish_memory(
    claim_id: str, request: FinishMemoryRequest, db: Database
) -> dict[str, str]:
    """Record confirmed events and close the receipt when both are saved."""
    claim = original_for_memory(db, claim_id, request)
    if claim["decision"] is None:
        raise HTTPException(409, "The claim has no recorded decision")
    if not claim["memory_pending"]:
        return {"status": "saved"}
    event_steps = db.execute(
        select(steps.c.content).where(
            steps.c.claim_id == claim_id,
            steps.c.thread_id == request.thread_id,
            steps.c.kind == "memory_save",
            steps.c.created_at >= claim["decided_at"],
        )
    ).scalars()
    recorded = {
        message["role"] for content in event_steps for message in content["messages"]
    }
    fresh = [message for message in request.messages if message.role not in recorded]
    if fresh:
        add_step(
            db, claim_id, request.thread_id, "memory_save", MemorySave(messages=fresh)
        )
    if recorded | {message.role for message in fresh} == {"customer", "agent"}:
        db.execute(
            update(claims)
            .where(claims.c.claim_id == claim_id)
            .values(memory_pending=False)
        )
        return {"status": "saved"}
    return {"status": "pending"}


@app.post(
    "/api/claims/{claim_id}/steps", operation_id="record_step", dependencies=agent_only
)
def record_step(claim_id: str, request: NewStepRequest, db: Database) -> Step:
    """Record one step of the agent's work on a claim."""
    if isinstance(request, SecurityStepRequest):
        return record_security_step(db, claim_id, request)
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id)
    require_attempt(claim, request.thread_id)
    thread_id = claim["thread_id"]
    if thread_id is None:
        raise HTTPException(409, f"Claim {claim_id} has not been started")
    return add_step(db, claim_id, thread_id, request.kind, request.content)


def record_security_step(
    db: Connection, claim_id: str, request: SecurityStepRequest
) -> Step:
    """Return the existing step if a saved screening result is replayed."""
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id)
    if claim["thread_id"] != request.thread_id:
        raise HTTPException(409, f"Claim {claim_id} is on a different attempt")
    saved = (
        db.execute(
            select(steps).where(
                steps.c.claim_id == claim_id,
                steps.c.thread_id == request.thread_id,
                steps.c.kind == request.kind,
            )
        )
        .mappings()
        .first()
    )
    if saved is not None:
        return Step(**saved)
    return add_step(db, claim_id, request.thread_id, request.kind, request.content)


@app.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
def list_steps(claim_id: str, handler: CurrentHandler, db: Database) -> list[Step]:
    """List the steps of the agent's work on a claim, in order."""
    find_claim(db, claim_id)
    query = select(steps).where(steps.c.claim_id == claim_id).order_by(steps.c.step_id)
    return [Step(**row) for row in db.execute(query).mappings()]


@app.post(
    "/api/claims/{claim_id}/decision",
    operation_id="record_decision",
    dependencies=agent_only,
)
def record_decision(claim_id: str, request: DecisionRequest, db: Database) -> Claim:
    """Record a claim's decision once; an approval waits to be paid or signed off."""
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id)
    require_attempt(claim, request.thread_id)
    require_revision(claim, request.revision)
    payout = round(request.payout, 2)
    if claim["decision"] is not None:
        if (claim["decision"], claim["payout"]) == (request.decision, payout):
            return to_claim(claim)
        raise HTTPException(409, f"Claim {claim_id} has already been decided")
    if claim["status"] != "in_progress":
        raise HTTPException(409, f"Claim {claim_id} is not in progress")
    calculation = calculation_for(claim, request)
    limit = db.execute(
        insert(decisions)
        .values(
            claim_id=claim_id,
            decision=request.decision,
            payout=payout,
            section=request.section,
            reason=request.reason,
            calculation=calculation,
            decided_by=request.decided_by,
            decided_at=now(),
            manual_version=manual_version(db),
            approval_limit=platform_settings.decision_limit(),
        )
        .returning(decisions.c.approval_limit)
    ).scalar_one()
    if request.decision != "approve":
        set_status(db, claim_id, "decided")
    elif needs_sign_off(payout, limit):
        set_status(db, claim_id, "waiting_for_sign_off")
    return to_claim(find_claim(db, claim_id))


@app.post(
    "/api/claims/{claim_id}/payout",
    operation_id="calculate_payout",
    dependencies=agent_only,
)
def calculate_payout(
    claim_id: str, request: ClaimPayoutRequest, db: Database
) -> Payout:
    """Work out the payout and retain the current attempt's calculation."""
    claim = find_claim(
        db, claim_id, lock=request.thread_id is not None, run_id=request.run_id
    )
    if request.thread_id is not None:
        require_attempt(claim, request.thread_id)
        require_revision(claim, request.revision)
    policy = find_policy(db, claim["record"]["policy_id"])
    result = work_out_payout(
        policy, plans(db), request, platform_settings.approval_limit(db)
    )
    if (
        claim["status"] == "in_progress"
        and request.thread_id is not None
        and claim["decision"] is None
    ):
        db.execute(
            update(claims)
            .where(claims.c.claim_id == claim_id)
            .values(latest_calculation=result.model_dump(exclude={"needs_sign_off"}))
        )
    return result


@app.post(
    "/api/claims/{claim_id}/payment", operation_id="pay_claim", dependencies=agent_only
)
def pay_claim(claim_id: str, request: PaymentRequest, db: Database) -> Claim:
    """Pay an approved claim its payout once, unless it needs a handler's sign-off."""
    check_payment_limit(request.amount, platform_settings.approval_limit(db))
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id)
    require_revision(claim, request.revision)
    if claim["decision"] != "approve":
        raise HTTPException(409, f"Claim {claim_id} has not been approved")
    amount = round(request.amount, 2)
    if claim["paid_at"] is not None:
        if amount == claim["amount"]:
            return to_claim(claim)
        raise HTTPException(409, f"Claim {claim_id} has already been paid")
    check_payment_limit(
        max(request.amount, amount), platform_settings.approval_limit(db, lock=True)
    )
    check_payment_limit(claim["payout"], claim["approval_limit"])
    if claim["status"] == "waiting_for_sign_off":
        raise HTTPException(403, "This payout is held")
    if amount != claim["payout"]:
        raise HTTPException(
            422, f"The amount must be the approved payout of {dollars(claim['payout'])}"
        )
    return settle_payment(db, claim_id, amount, now())


def check_payment_limit(amount: float, limit: float) -> None:
    """Apply the configured ceiling before looking for an approved claim."""
    if needs_sign_off(amount, limit):
        raise HTTPException(
            403, f"A payout over {dollars(limit)} needs a handler's sign-off"
        )


def can_settle_sign_off(
    claim: RowMapping | dict[str, Any], current_limit: float
) -> bool:
    """Identify an original approval that a senior handler can settle."""
    if (
        claim["evaluation_id"] is not None
        or claim["decision"] != "approve"
        or claim["paid_at"] is not None
    ):
        return False
    if claim["status"] == "waiting_for_sign_off":
        return True
    return claim["status"] == "in_progress" and (
        claim["signed_off_by"] is not None
        or needs_sign_off(claim["payout"], current_limit)
    )


def settle_payment(db: Connection, claim_id: str, amount: float, paid_at: str) -> Claim:
    """Save the payment and complete the claim in the caller's transaction."""
    db.execute(
        insert(payments).values(claim_id=claim_id, amount=amount, paid_at=paid_at)
    )
    set_status(db, claim_id, "decided")
    return to_claim(find_claim(db, claim_id))


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(claim_id: str, handler: ReviewHandler, http: Request) -> Claim:
    """Record the senior handler's sign-off and pay the approved payout."""
    with engine().begin() as db:
        receipt = copilot_receipt(http)
        turn = (
            copilot_records.take_action(db, handler.handler_id, receipt)
            if receipt
            else None
        )
        claim = lock_review(db, claim_id, http.headers.get("If-Match"))
        if turn is not None:
            copilot_records.require_confirmation(turn, claim)
        signed = sign_off_payment(db, claim, handler)
    agent_service.resume_claim(claim_id)
    return signed


def sign_off_payment(db: Connection, claim: RowMapping, handler: Handler) -> Claim:
    """Settle the locked approval in the handler's transaction."""
    claim_id = claim["claim_id"]
    run_records.preserve_hold(db, claim)
    db.execute(
        update(claims).where(claims.c.claim_id == claim_id).values(memory_pending=False)
    )
    paid_at = now()
    if claim["signed_off_by"] is None:
        db.execute(
            update(decisions)
            .where(decisions.c.claim_id == claim_id)
            .values(signed_off_by=handler.name, signed_off_at=paid_at)
        )
    return settle_payment(db, claim_id, claim["payout"], paid_at)


def lock_review(db: Connection, claim_id: str, revision: str | None) -> RowMapping:
    """Lock a reviewable claim and re-read its decision after taking the lock."""
    run_records.lock_review_runs(db, claim_id)
    claim = find_claim(db, claim_id, lock=True)
    if claim["evaluation_id"] is not None:
        raise HTTPException(409, "Evaluation claims cannot be reviewed")
    if not can_settle_sign_off(claim, platform_settings.approval_limit(db, lock=True)):
        raise HTTPException(409, f"Claim {claim_id} is not waiting for sign-off")
    if revision is not None and revision != f'"{claim["revision"] or 0}"':
        raise HTTPException(
            409, "The decision changed. Reload the claim before reviewing it"
        )
    return claim


@app.post("/api/claims/{claim_id}/send-back", operation_id="send_back_claim")
def send_back_claim(
    claim_id: str, request: SendBackRequest, handler: ReviewHandler, http: Request
) -> Claim:
    """Return a held approval for a fresh decision with a senior handler's note."""
    with engine().begin() as db:
        receipt = copilot_receipt(http)
        if receipt:
            copilot_records.take_action(db, handler.handler_id, receipt)
            if copilot_records.saved_action(db, receipt) == claim_id:
                return to_claim(find_claim(db, claim_id))
        claim = lock_review(db, claim_id, http.headers.get("If-Match"))
        if claim["status"] != "waiting_for_sign_off" or claim["signed_off_by"]:
            raise HTTPException(409, f"Claim {claim_id} is not waiting for review")
        review = ClaimReview(note=request.note, handler=handler.name, reviewed_at=now())
        run_records.preserve_hold(db, claim)
        claim_reviews.record_return(db, claim, review)
        add_step(db, claim_id, claim["thread_id"], "claim_review", review)
        returned = to_claim(find_claim(db, claim_id))
        if receipt:
            copilot_records.save_action(db, receipt, claim_id)
    agent_service.resume_claim(claim_id)
    return returned


@app.post(
    "/api/claims/{claim_id}/take-review",
    operation_id="take_review_claim",
    dependencies=agent_only,
)
def take_review_claim(claim_id: str, request: TakeClaimRequest, db: Database) -> Claim:
    """Give one worker the lease for a returned claim."""
    improvement_budget.admit_run(db, request.run_id)
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id or "")
    claim_reviews.take(db, claim, request.owner, request.run_id)
    return to_claim(find_claim(db, claim_id))


@app.put(
    "/api/claims/{claim_id}/review-heartbeat",
    operation_id="review_heartbeat",
    dependencies=agent_only,
)
def review_heartbeat(
    claim_id: str, request: ClaimHeartbeat, db: Database
) -> dict[str, str]:
    """Renew or release the review lease for the current owner."""
    run_id = find_claim(db, claim_id)["review_run_id"] if request.working else None
    claim = find_claim(db, claim_id, lock=True, run_id=run_id or "")
    claim_reviews.update_heartbeat(db, claim, request.owner, request.working)
    return {"status": "recorded"}


@app.post(
    "/api/claims/{claim_id}/hold-limit-payment",
    operation_id="hold_limit_payment",
    dependencies=agent_only,
)
def hold_limit_payment(claim_id: str, request: LimitHoldRequest, db: Database) -> Claim:
    """Hold an unpaid approval refused under the current approval limit."""
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id)
    require_revision(claim, request.revision)
    if claim["paid_at"] is not None and claim["signed_off_by"] is not None:
        return to_claim(claim)
    if (
        claim["decision"] != "approve"
        or claim["paid_at"] is not None
        or claim["status"] not in {"in_progress", "waiting_for_sign_off"}
    ):
        raise HTTPException(409, "Only an unpaid approval can be held")
    if claim["status"] == "in_progress":
        limit = platform_settings.approval_limit(db, lock=True)
        if claim["evaluation_id"] is None and not needs_sign_off(
            claim["payout"], min(limit, claim["approval_limit"])
        ):
            raise HTTPException(409, "This payout does not need sign-off")
        set_status(db, claim_id, "waiting_for_sign_off")
    return to_claim(find_claim(db, claim_id))


@app.post(
    "/api/claims/{claim_id}/hold-gateway-payment",
    operation_id="hold_gateway_payment",
    dependencies=agent_only,
)
def hold_gateway_payment(
    claim_id: str, request: GatewayHoldRequest, db: Database
) -> Claim:
    """Hold an unpaid approval after the Gateway refused payment."""
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id)
    require_revision(claim, request.revision)
    if claim["decision"] != "approve" or claim["paid_at"] is not None:
        raise HTTPException(409, "Only an unpaid approval can be held by the Gateway")
    if claim["status"] == "waiting_for_sign_off" and claim["gateway_hold"] is not None:
        return to_claim(claim)
    if claim["status"] != "in_progress":
        raise HTTPException(
            409, "Only an in-progress approval can be held by the Gateway"
        )
    db.execute(
        update(decisions)
        .where(decisions.c.claim_id == claim_id)
        .values(gateway_hold=request.reason)
    )
    add_step(
        db,
        claim_id,
        claim["thread_id"],
        "refusal",
        Refusal(source="Gateway", reason=request.reason),
    )
    set_status(db, claim_id, "waiting_for_sign_off")
    return to_claim(find_claim(db, claim_id))


@app.post(
    "/api/claims/{claim_id}/failure", operation_id="fail_claim", dependencies=agent_only
)
def fail_claim(claim_id: str, request: FailureRequest, db: Database) -> Claim:
    """Put a failed claim back in the queue, or leave it for a handler after two."""
    claim = find_claim(db, claim_id, lock=True, run_id=request.run_id)
    require_revision(claim, request.revision)
    if claim["status"] != "in_progress":
        raise HTTPException(409, f"Claim {claim_id} is not in progress")
    if claim["decision"] is not None:
        raise HTTPException(409, f"Claim {claim_id} has already been decided")
    add_step(db, claim_id, claim["thread_id"], "error", request.reason)
    status = "left_for_handler" if claim["attempts"] >= ATTEMPTS else "waiting"
    set_status(db, claim_id, status)
    return to_claim(find_claim(db, claim_id))


@app.post("/api/runs", operation_id="start_run", dependencies=agent_only)
def start_run(request: RunRequest, db: Database) -> Run:
    """Create an improver's nested evaluation under its parent's admission."""
    if request.round_id is None:
        raise HTTPException(422, "The app admits standalone work")
    if request.tiny is not False:
        raise HTTPException(422, "Round work must be a whole-set evaluation")
    work_admission.admit_round_evaluation(db, request.round_id)
    return create_run(db, request)


def create_run(db: Connection, request: RunRequest) -> Run:
    """Pin run records and create isolated evaluation copies."""
    if request.round_id is not None:
        if request.set_name is None:
            raise HTTPException(422, "Round work must be a whole-set evaluation")
        quote = improvements.evaluation_quote(
            db,
            request.round_id,
            EvaluationQuote(
                set_name=request.set_name,
                configuration_version=request.configuration_version,
            ),
        )
        if not quote.allowed:
            raise HTTPException(409, quote.reason)
    run = run_records.start_run(db, request)
    if request.kind == "evaluation":
        evaluations.create_copies(db, run.run_id, request)
    return run_records.get_run(db, run.run_id)


@app.get("/api/runs/latest", operation_id="get_latest_run")
def get_latest_run(handler: CurrentHandler, db: Database) -> Run | None:
    """Get the newest dispatch and its member progress."""
    return run_records.latest(db)


@app.get(
    "/api/runs/stopped-threads",
    operation_id="stopped_evaluation_threads",
    dependencies=agent_only,
)
def stopped_evaluation_threads(db: Database) -> list[str]:
    """List only checkpoint threads from stopped evaluation runs."""
    return run_records.evaluation_threads(db, None)


@app.get("/api/runs/active", operation_id="get_active_run")
def get_active_run(handler: CurrentHandler, db: Database) -> Run | None:
    """Read the running dispatch or evaluation for shared dashboard busy controls."""
    return run_records.active(db)


@app.get("/api/runs/{run_id}", operation_id="get_run", dependencies=agent_only)
def get_run(run_id: str, db: Database) -> Run:
    """Read a run with its own measurements and member results."""
    return run_records.get_run(db, run_id)


@app.post("/api/runs/{run_id}/take", operation_id="take_run", dependencies=agent_only)
def take_run(run_id: str, db: Database) -> Run:
    """Claim one committed standalone run for its accepting worker."""
    work_admission.take_run(db, run_id)
    return run_records.get_run(db, run_id)


@app.put(
    "/api/runs/{run_id}/heartbeat",
    operation_id="run_heartbeat",
    dependencies=agent_only,
)
def run_heartbeat(run_id: str, db: Database) -> dict[str, str]:
    """Renew a running standalone run without reopening closed work."""
    work_admission.heartbeat_run(db, run_id)
    return {"status": "recorded"}


@app.get(
    "/api/runs/{run_id}/threads",
    operation_id="get_run_threads",
    dependencies=agent_only,
)
def get_run_threads(run_id: str, db: Database) -> list[str]:
    """List evaluation checkpoint threads without reading expected outcomes."""
    row = run_records.run_row(db, run_id)
    if row["kind"] != "evaluation" or row["status"] == "running":
        raise HTTPException(
            409, "Only stopped or finished evaluations can clear checkpoints"
        )
    return run_records.evaluation_threads(db, run_id)


@app.put(
    "/api/runs/{run_id}/claims/{claim_id}",
    operation_id="record_run_claim",
    dependencies=agent_only,
)
def record_run_claim(
    run_id: str, claim_id: str, request: RunClaimRequest, db: Database
) -> dict[str, str]:
    """Record selected membership or a worker's progress once per claim."""
    run_records.record_member(db, run_id, claim_id, request)
    return {"status": "recorded"}


@app.put(
    "/api/runs/{run_id}/selection",
    operation_id="record_run_selection",
    dependencies=agent_only,
)
def record_run_selection(
    run_id: str, request: RunSelectionRequest, db: Database
) -> dict[str, str]:
    """Register the dispatcher's ordered selection in one transaction."""
    run_records.select_members(db, run_id, request.claim_ids)
    return {"status": "recorded"}


@app.post(
    "/api/runs/{run_id}/finish", operation_id="finish_run", dependencies=agent_only
)
def finish_run(run_id: str) -> Run:
    """Finish a run with a summary built entirely from backend records."""
    return evaluation_completion.finish_run(run_id)


@app.post(
    "/api/claims/{claim_id}/model-calls",
    operation_id="record_model_call",
    dependencies=agent_only,
)
def record_model_call(
    claim_id: str, request: ClaimModelCall, db: Database
) -> dict[str, str]:
    """Record the provider's usage and cost for one received model reply."""
    claim = find_claim(db, claim_id)
    thread_id = request.thread_id or claim["thread_id"]
    if thread_id is None:
        raise HTTPException(409, "Claim has not been started")
    if thread_id != claim["thread_id"] and (
        request.run_id is None
        or not run_records.has_work_slice(db, request.run_id, (claim_id, thread_id))
    ):
        raise HTTPException(409, "Claim work belongs to a different attempt")
    run_records.record_model_call(db, claim_id, thread_id, request)
    return {"status": "recorded"}


@app.post(
    "/api/claims/{claim_id}/admission",
    operation_id="admit_claim",
    dependencies=agent_only,
)
def admit_claim(claim_id: str, request: RunAdmission, db: Database) -> dict[str, str]:
    """Check stop and spending immediately before new claim model work."""
    claim = find_claim(db, claim_id)
    require_revision(claim, request.revision)
    if claim["evaluation_id"] is not None and request.run_id != claim["evaluation_id"]:
        raise HTTPException(409, "Evaluation work must use its own run")
    improvement_budget.admit_run(db, request.run_id)
    return {"status": "accepted"}


@app.post("/api/runs/{run_id}/stop", operation_id="stop_run", dependencies=agent_only)
def stop_run(run_id: str, request: StoppedWork, db: Database) -> Run:
    """Close one drained run without interrupting another accepted operation."""
    return run_records.stop(db, run_id, request.reason)


@app.put(
    "/api/claims/{claim_id}/work-slices/{slice_id}",
    operation_id="record_work_slice",
    dependencies=agent_only,
)
def record_work_slice(
    claim_id: str, slice_id: str, request: WorkSliceRequest, db: Database
) -> dict[str, str]:
    """Record cumulative active seconds for one execution of a claim."""
    claim = find_claim(db, claim_id)
    if claim["thread_id"] is None:
        raise HTTPException(409, "Claim has not been started")
    run_records.record_work_slice(db, claim_id, claim["thread_id"], slice_id, request)
    return {"status": "recorded"}


install_dashboard(app)
handler = Mangum(app, lifespan="off")
