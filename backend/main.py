"""The UnderCover backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query
from mangum import Mangum
from sqlalchemy import Connection, RowMapping, insert, select, update

from backend import (
    agent_service,
    configurations,
    evaluation_completion,
    evaluations,
    improvement_budget,
    improvements,
)
from backend import runs as run_records
from backend import settings as platform_settings
from backend.agent_contract import agent_contract
from backend.auth import CurrentHandler, SeniorHandler, agent_or_handler, require_agent
from backend.clock import now
from backend.configuration_models import ClaimConfiguration, ConfigurationHistory
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
)
from backend.dependencies import Database
from backend.documents import search, sections, titles
from backend.improvement_models import EvaluationQuote
from backend.improvement_routes import router as improvement_router
from backend.models import (
    Claim,
    ClaimSummary,
    Decision,
    DecisionRequest,
    DispatchRequest,
    DocumentName,
    Evaluation,
    EvaluationDetail,
    EvaluationRequest,
    FailureRequest,
    GatewayHoldRequest,
    Handler,
    ModelCallRequest,
    Payment,
    PaymentRequest,
    Payout,
    PayoutRequest,
    Plans,
    Policy,
    PreviousClaim,
    Run,
    RunAdmission,
    RunClaimRequest,
    RunRequest,
    RunSelectionRequest,
    Section,
    SectionMatch,
    SecurityStepRequest,
    StartRequest,
    Status,
    Step,
    StepRequest,
    WorkSliceRequest,
)
from backend.payout import needs_sign_off, work_out_payout
from backend.settings_models import PlatformSettings, SettingsView

ATTEMPTS = 2


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Seed the database on startup if it has no tables yet."""
    if not running_on_lambda() and not is_seeded(engine()):
        seed(engine())
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)
app.include_router(improvement_router)
agent_only = [Depends(require_agent)]
shared_read = [Depends(agent_or_handler)]


@app.post("/api/dispatches", operation_id="start_dispatch", status_code=202)
def start_dispatch(request: DispatchRequest, handler: CurrentHandler) -> dict[str, str]:
    """Ask the agent service to accept a dispatch without creating a run first."""
    return agent_service.start_dispatch(request.concurrency)


@app.post("/api/evaluations", operation_id="start_evaluation", status_code=202)
def start_evaluation(
    request: EvaluationRequest, handler: CurrentHandler
) -> dict[str, str]:
    """Ask the agent service to accept an isolated evaluation."""
    return agent_service.start_evaluation(
        request.set_name, request.tiny, request.concurrency
    )


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


def find_claim(db: Connection, claim_id: str, lock: bool = False) -> RowMapping:
    """Return one claim's row, or raise a 404."""
    query = claim_view.where(claims.c.claim_id == claim_id)
    if lock:
        improvement_budget.guard_claim_write(db, claim_id)
        if db.dialect.name == "sqlite":
            db.execute(
                update(claims)
                .where(claims.c.claim_id == claim_id)
                .values(thread_id=claims.c.thread_id)
            )
        query = query.with_for_update(of=claims)
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


def to_summary(row: RowMapping) -> ClaimSummary:
    """Build a claim's line in the queue from its row."""
    return ClaimSummary(
        **row["record"]
        | {
            "status": row["status"],
            "customer_name": row["customer_name"],
            "decision": row["decision"],
            "payout": row["payout"],
        }
    )


def to_claim(row: RowMapping | dict[str, Any]) -> Claim:
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
        }
    )


def add_step(
    db: Connection, claim_id: str, thread_id: str, kind: str, content: str
) -> Step:
    """Add one step of the agent's work to a claim."""
    query = (
        insert(steps)
        .values(
            claim_id=claim_id,
            thread_id=thread_id,
            kind=kind,
            content=content,
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
    """Describe the eight claim agent operations for the Gateway."""
    return agent_contract(app)


@app.get("/api/claims", operation_id="list_claims", dependencies=shared_read)
def list_claims(db: Database) -> list[ClaimSummary]:
    """List every claim in the queue, in id order."""
    rows = db.execute(
        claim_view.where(claims.c.evaluation_id.is_(None)).order_by(claims.c.claim_id)
    ).mappings()
    return [to_summary(row) for row in rows]


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


@app.get(
    "/api/documents/search", operation_id="search_documents", dependencies=agent_only
)
def search_documents(
    query: Annotated[str, Query(min_length=1)], db: Database
) -> list[SectionMatch]:
    """Find the sections of the wording and the manual that mention a word or phrase."""
    return search(db, query)


@app.get(
    "/api/documents/{document}/sections/{number}",
    operation_id="read_section",
    dependencies=agent_only,
)
def read_section(document: DocumentName, number: int, db: Database) -> Section:
    """Read one section of the policy wording or the claims manual in full."""
    for section in sections(db, document):
        if section.number == number:
            return section
    title = titles(db)[document].lower()
    raise HTTPException(404, f"Section {number} of the {title} not found")


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers(handler: CurrentHandler, db: Database) -> list[Handler]:
    """List the claims handlers."""
    rows = db.execute(select(handlers).order_by(handlers.c.handler_id)).mappings()
    return [Handler(**row) for row in rows]


@app.post(
    "/api/claims/{claim_id}/start", operation_id="start_claim", dependencies=agent_only
)
def start_claim(claim_id: str, request: StartRequest, db: Database) -> Claim:
    """Start work on a waiting claim, which moves it to in progress."""
    improvement_budget.admit_run(db, find_claim(db, claim_id)["evaluation_id"])
    if find_claim(db, claim_id, lock=True)["status"] != "waiting":
        raise HTTPException(409, f"Claim {claim_id} is not waiting")
    db.execute(
        update(claims)
        .where(claims.c.claim_id == claim_id)
        .values(
            status="in_progress",
            thread_id=request.thread_id,
            attempts=claims.c.attempts + 1,
        )
    )
    return to_claim(find_claim(db, claim_id))


@app.post(
    "/api/claims/{claim_id}/steps", operation_id="record_step", dependencies=agent_only
)
def record_step(
    claim_id: str, request: StepRequest | SecurityStepRequest, db: Database
) -> Step:
    """Record one step of the agent's work on a claim."""
    if isinstance(request, SecurityStepRequest):
        return record_security_step(db, claim_id, request)
    improvement_budget.guard_claim_write(db, claim_id)
    thread_id = find_claim(db, claim_id)["thread_id"]
    if thread_id is None:
        raise HTTPException(409, f"Claim {claim_id} has not been started")
    return add_step(db, claim_id, thread_id, request.kind, request.content)


def record_security_step(
    db: Connection, claim_id: str, request: SecurityStepRequest
) -> Step:
    """Return the existing step if a saved screening result is replayed."""
    claim = find_claim(db, claim_id, lock=True)
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
    claim = find_claim(db, claim_id, lock=True)
    payout = round(request.payout, 2)
    if claim["decision"] is not None:
        if (claim["decision"], claim["payout"]) == (request.decision, payout):
            return to_claim(claim)
        raise HTTPException(409, f"Claim {claim_id} has already been decided")
    if claim["status"] != "in_progress":
        raise HTTPException(409, f"Claim {claim_id} is not in progress")
    limit = db.execute(
        insert(decisions)
        .values(
            claim_id=claim_id,
            decision=request.decision,
            payout=payout,
            section=request.section,
            reason=request.reason,
            decided_at=now(),
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
def calculate_payout(claim_id: str, request: PayoutRequest, db: Database) -> Payout:
    """Work out an approval's payout with UnderCover's payout rules; records nothing."""
    policy = find_policy(db, to_claim(find_claim(db, claim_id)).policy_id)
    return work_out_payout(
        policy, plans(db), request, platform_settings.approval_limit(db)
    )


@app.post(
    "/api/claims/{claim_id}/payment", operation_id="pay_claim", dependencies=agent_only
)
def pay_claim(claim_id: str, request: PaymentRequest, db: Database) -> Claim:
    """Pay an approved claim its payout once, unless it needs a handler's sign-off."""
    check_payment_limit(request.amount, platform_settings.approval_limit(db))
    claim = find_claim(db, claim_id, lock=True)
    if claim["decision"] != "approve":
        raise HTTPException(409, f"Claim {claim_id} has not been approved")
    if claim["status"] == "decided" and claim["paid_at"] is None:
        # A joined payment can be newer than the snapshot taken before the row lock.
        claim = find_claim(db, claim_id)
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
def sign_off_claim(claim_id: str, handler: SeniorHandler, db: Database) -> Claim:
    """Record the senior handler's sign-off and pay the approved payout."""
    claim = find_claim(db, claim_id, lock=True)
    if claim["evaluation_id"] is not None:
        raise HTTPException(409, "Evaluation claims cannot be signed off")
    if not can_settle_sign_off(claim, platform_settings.approval_limit(db, lock=True)):
        raise HTTPException(409, f"Claim {claim_id} is not waiting for sign-off")
    paid_at = now()
    if claim["signed_off_by"] is None:
        db.execute(
            update(decisions)
            .where(decisions.c.claim_id == claim_id)
            .values(signed_off_by=handler.name, signed_off_at=paid_at)
        )
    return settle_payment(db, claim_id, claim["payout"], paid_at)


@app.post(
    "/api/claims/{claim_id}/hold-evaluation-payment",
    operation_id="hold_evaluation_payment",
    dependencies=agent_only,
)
def hold_evaluation_payment(claim_id: str, db: Database) -> Claim:
    """Retain a held result when the agent's payment for an evaluation was refused."""
    run_id = find_claim(db, claim_id)["evaluation_id"]
    if run_id is None:
        raise HTTPException(409, "Only an unpaid evaluation approval can be held")
    run_records.validate_run(db, run_id)
    claim = find_claim(db, claim_id, lock=True)
    if (
        claim["decision"] != "approve"
        or claim["paid_at"] is not None
        or claim["status"] not in {"in_progress", "waiting_for_sign_off"}
    ):
        raise HTTPException(409, "Only an unpaid evaluation approval can be held")
    if claim["status"] == "in_progress":
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
    find_claim(db, claim_id, lock=True)
    claim = find_claim(db, claim_id)
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
        f"Gateway refused: {request.reason}",
    )
    set_status(db, claim_id, "waiting_for_sign_off")
    return to_claim(find_claim(db, claim_id))


@app.post(
    "/api/claims/{claim_id}/failure", operation_id="fail_claim", dependencies=agent_only
)
def fail_claim(claim_id: str, request: FailureRequest, db: Database) -> Claim:
    """Put a failed claim back in the queue, or leave it for a handler after two."""
    claim = find_claim(db, claim_id, lock=True)
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
    """Create a run recording its execution configuration."""
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


@app.post("/api/runs/stop", operation_id="stop_runs", dependencies=agent_only)
def stop_runs(db: Database) -> list[str]:
    """Mark abandoned runs stopped and return evaluation threads for cleanup."""
    return run_records.stop_runs(db)


@app.get("/api/runs/active", operation_id="get_active_run")
def get_active_run(handler: CurrentHandler, db: Database) -> Run | None:
    """Read the running dispatch or evaluation for shared dashboard busy controls."""
    return run_records.active(db)


@app.get("/api/runs/{run_id}", operation_id="get_run", dependencies=agent_only)
def get_run(run_id: str, db: Database) -> Run:
    """Read a run with its own measurements and member results."""
    return run_records.get_run(db, run_id)


@app.get(
    "/api/runs/{run_id}/threads",
    operation_id="get_run_threads",
    dependencies=agent_only,
)
def get_run_threads(run_id: str, db: Database) -> list[str]:
    """List evaluation checkpoint threads without reading expected outcomes."""
    run_records.run_row(db, run_id)
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
    claim_id: str, request: ModelCallRequest, db: Database
) -> dict[str, str]:
    """Record the provider's usage and cost for one received model reply."""
    claim = find_claim(db, claim_id)
    if claim["thread_id"] is None:
        raise HTTPException(409, "Claim has not been started")
    run_records.record_model_call(db, claim_id, claim["thread_id"], request)
    return {"status": "recorded"}


@app.post(
    "/api/claims/{claim_id}/admission",
    operation_id="admit_claim",
    dependencies=agent_only,
)
def admit_claim(claim_id: str, request: RunAdmission, db: Database) -> dict[str, str]:
    """Check stop and spending immediately before new claim model work."""
    claim = find_claim(db, claim_id)
    if claim["evaluation_id"] is not None and request.run_id != claim["evaluation_id"]:
        raise HTTPException(409, "Evaluation work must use its own run")
    improvement_budget.admit_run(db, request.run_id)
    return {"status": "accepted"}


@app.post("/api/runs/{run_id}/stop", operation_id="stop_run", dependencies=agent_only)
def stop_run(run_id: str, db: Database) -> Run:
    """Close one drained run without interrupting another accepted operation."""
    return run_records.finish_run(db, run_id, "stopped")


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
