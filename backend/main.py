"""The UnderCover backend."""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from sqlalchemy import Connection, RowMapping, delete, insert, select, update

from backend import agent_service
from backend import runs as run_records
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
    runs,
    seed,
    settings,
    steps,
    todos,
)
from backend.documents import contents, search, sections, titles
from backend.models import (
    Claim,
    ClaimSummary,
    Decision,
    DecisionRequest,
    DocumentName,
    FailureRequest,
    FinishRequest,
    Handler,
    Instructions,
    ModelCallRequest,
    Payment,
    PaymentRequest,
    Payout,
    PayoutRequest,
    Plans,
    Policy,
    PreviousClaim,
    Run,
    RunRequest,
    Section,
    SectionMatch,
    SignOffRequest,
    StartRequest,
    Status,
    Step,
    StepRequest,
    Todo,
    TodosRequest,
    WorkSliceRequest,
)
from backend.payout import needs_sign_off, work_out_payout

INSTRUCTIONS = Path(__file__).parent / "instructions"
ATTEMPTS = 2


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Seed the database on startup if it has no tables yet."""
    if not is_seeded(engine()):
        seed(engine())
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


def database() -> Iterator[Connection]:
    """Open the database for one request, in a transaction committed at its end."""
    with engine().begin() as connection:
        yield connection


# Committed before the response is sent and before any background task runs.
Database = Annotated[Connection, Depends(database, scope="function")]


def now() -> str:
    """Return the current time in UTC, to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def dollars(amount: float) -> str:
    """Format an amount like $1,250.00."""
    return f"${amount:,.2f}"


def approval_limit(db: Connection) -> float:
    """Return the largest payout that can be paid without a handler's sign-off."""
    return db.execute(select(settings.c.payout_approval_limit)).scalar_one()


def plans(db: Connection) -> Plans:
    """Return the plans and add-ons, and the benefits the deductible applies to."""
    return Plans(**db.execute(select(settings.c.plans)).scalar_one())


def find_claim(db: Connection, claim_id: str) -> RowMapping:
    """Return one claim's row, or raise a 404."""
    query = claim_view.where(claims.c.claim_id == claim_id)
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


def find_run(db: Connection, run_id: str) -> Run:
    """Return one run with its to-do list, or raise a 404."""
    row = db.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()
    if row is None:
        raise HTTPException(404, f"Run {run_id} not found")
    query = (
        select(todos.c.content, todos.c.status)
        .where(todos.c.run_id == run_id)
        .order_by(todos.c.position)
    )
    items = [Todo(**todo) for todo in db.execute(query).mappings()]
    return Run(**row, todos=items)


def running_run(db: Connection, run_id: str) -> Run:
    """Return a run that is still running, or raise a 404 or a 409."""
    run = find_run(db, run_id)
    if run.status != "running":
        raise HTTPException(409, f"Run {run_id} has finished")
    return run


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def list_claims(db: Database) -> list[ClaimSummary]:
    """List every claim in the queue, in id order."""
    rows = db.execute(claim_view.order_by(claims.c.claim_id)).mappings()
    return [to_summary(row) for row in rows]


@app.get("/api/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str, db: Database) -> Claim:
    """Get one claim in full: the story, the items claimed and the documents."""
    return to_claim(find_claim(db, claim_id))


@app.get("/api/policies/{policy_id}", operation_id="get_policy")
def get_policy(policy_id: str, db: Database) -> Policy:
    """Get a policy: the plan, add-ons, purchase date, trip and travelers."""
    return find_policy(db, policy_id)


@app.get("/api/customers/{customer_id}/claims", operation_id="get_claim_history")
def get_claim_history(customer_id: str, db: Database) -> list[PreviousClaim]:
    """Get a customer's earlier claims, with the outcome and the amount paid."""
    query = select(customers.c.record).where(customers.c.customer_id == customer_id)
    record = db.execute(query).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, f"Customer {customer_id} not found")
    return [PreviousClaim(**claim) for claim in record["previous_claims"]]


@app.get("/api/instructions", operation_id="get_instructions")
def get_instructions(db: Database) -> Instructions:
    """Get the agent's instructions for running the queue and for working a claim."""
    queue = (INSTRUCTIONS / "queue.md").read_text(encoding="utf-8")
    claim = (INSTRUCTIONS / "claim.md").read_text(encoding="utf-8")
    return Instructions(queue=queue, claim=f"{claim}\n{contents(db)}")


@app.get("/api/documents/search", operation_id="search_documents")
def search_documents(
    query: Annotated[str, Query(min_length=1)], db: Database
) -> list[SectionMatch]:
    """Find the sections of the wording and the manual that mention a word or phrase."""
    return search(db, query)


@app.get("/api/documents/{document}/sections/{number}", operation_id="read_section")
def read_section(document: DocumentName, number: int, db: Database) -> Section:
    """Read one section of the policy wording or the claims manual in full."""
    for section in sections(db, document):
        if section.number == number:
            return section
    title = titles(db)[document].lower()
    raise HTTPException(404, f"Section {number} of the {title} not found")


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers(db: Database) -> list[Handler]:
    """List the claims handlers."""
    rows = db.execute(select(handlers).order_by(handlers.c.handler_id)).mappings()
    return [Handler(**row) for row in rows]


@app.post("/api/claims/{claim_id}/start", operation_id="start_claim")
def start_claim(claim_id: str, request: StartRequest, db: Database) -> Claim:
    """Start work on a waiting claim, which moves it to in progress."""
    if find_claim(db, claim_id)["status"] != "waiting":
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


@app.post("/api/claims/{claim_id}/steps", operation_id="record_step")
def record_step(claim_id: str, request: StepRequest, db: Database) -> Step:
    """Record one step of the agent's work on a claim."""
    thread_id = find_claim(db, claim_id)["thread_id"]
    if thread_id is None:
        raise HTTPException(409, f"Claim {claim_id} has not been started")
    return add_step(db, claim_id, thread_id, request.kind, request.content)


@app.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
def list_steps(claim_id: str, db: Database) -> list[Step]:
    """List the steps of the agent's work on a claim, in order."""
    find_claim(db, claim_id)
    query = select(steps).where(steps.c.claim_id == claim_id).order_by(steps.c.step_id)
    return [Step(**row) for row in db.execute(query).mappings()]


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def record_decision(claim_id: str, request: DecisionRequest, db: Database) -> Claim:
    """Record a claim's decision once; an approval waits to be paid or signed off."""
    claim = find_claim(db, claim_id)
    payout = round(request.payout, 2)
    if claim["decision"] is not None:
        if (claim["decision"], claim["payout"]) == (request.decision, payout):
            return to_claim(claim)
        raise HTTPException(409, f"Claim {claim_id} has already been decided")
    if claim["status"] != "in_progress":
        raise HTTPException(409, f"Claim {claim_id} is not in progress")
    db.execute(
        insert(decisions).values(
            claim_id=claim_id,
            decision=request.decision,
            payout=payout,
            section=request.section,
            reason=request.reason,
            decided_at=now(),
        )
    )
    if request.decision != "approve":
        set_status(db, claim_id, "decided")
    elif needs_sign_off(payout, approval_limit(db)):
        set_status(db, claim_id, "waiting_for_sign_off")
    return to_claim(find_claim(db, claim_id))


@app.post("/api/claims/{claim_id}/payout", operation_id="calculate_payout")
def calculate_payout(claim_id: str, request: PayoutRequest, db: Database) -> Payout:
    """Work out an approval's payout with UnderCover's payout rules; records nothing."""
    policy = find_policy(db, to_claim(find_claim(db, claim_id)).policy_id)
    return work_out_payout(policy, plans(db), request, approval_limit(db))


@app.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
def pay_claim(claim_id: str, request: PaymentRequest, db: Database) -> Claim:
    """Pay an approved claim its payout once, unless it needs a handler's sign-off."""
    claim = find_claim(db, claim_id)
    if claim["decision"] != "approve":
        raise HTTPException(409, f"Claim {claim_id} has not been approved")
    amount = round(request.amount, 2)
    if claim["paid_at"] is not None:
        if amount == claim["amount"]:
            return to_claim(claim)
        raise HTTPException(409, f"Claim {claim_id} has already been paid")
    if claim["status"] == "waiting_for_sign_off":
        raise HTTPException(
            403,
            f"A payout over {dollars(approval_limit(db))} needs a handler's sign-off",
        )
    if amount != claim["payout"]:
        raise HTTPException(
            422, f"The amount must be the approved payout of {dollars(claim['payout'])}"
        )
    db.execute(
        insert(payments).values(
            claim_id=claim_id, amount=claim["payout"], paid_at=now()
        )
    )
    set_status(db, claim_id, "decided")
    return to_claim(find_claim(db, claim_id))


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(
    claim_id: str, request: SignOffRequest, db: Database, background: BackgroundTasks
) -> Claim:
    """Sign off a held payout as a handler, so the agent picks the claim up and pays."""
    claim = find_claim(db, claim_id)
    if claim["status"] != "waiting_for_sign_off":
        raise HTTPException(409, f"Claim {claim_id} is not waiting for sign-off")
    query = select(handlers).where(handlers.c.handler_id == request.handler_id)
    row = db.execute(query).mappings().first()
    if row is None:
        raise HTTPException(404, f"Handler {request.handler_id} not found")
    handler = Handler(**row)
    if not handler.can_approve_over_limit:
        raise HTTPException(
            403,
            f"{handler.name} can't sign off payouts over {dollars(approval_limit(db))}",
        )
    db.execute(
        update(decisions)
        .where(decisions.c.claim_id == claim_id)
        .values(signed_off_by=handler.name, signed_off_at=now())
    )
    set_status(db, claim_id, "in_progress")
    background.add_task(agent_service.resume_claim, claim_id)
    return to_claim(find_claim(db, claim_id))


@app.post("/api/claims/{claim_id}/failure", operation_id="fail_claim")
def fail_claim(claim_id: str, request: FailureRequest, db: Database) -> Claim:
    """Put a failed claim back in the queue, or leave it for a handler after two."""
    claim = find_claim(db, claim_id)
    if claim["status"] != "in_progress":
        raise HTTPException(409, f"Claim {claim_id} is not in progress")
    if claim["decision"] is not None:
        raise HTTPException(409, f"Claim {claim_id} has already been decided")
    add_step(db, claim_id, claim["thread_id"], "error", request.reason)
    status = "left_for_handler" if claim["attempts"] >= ATTEMPTS else "waiting"
    set_status(db, claim_id, status)
    return to_claim(find_claim(db, claim_id))


@app.post("/api/runs", operation_id="start_run")
def start_run(request: RunRequest, db: Database) -> Run:
    """Start a run of the agent on the queue, with an empty to-do list."""
    run_id = str(uuid.uuid4())
    db.execute(
        insert(runs).values(
            run_id=run_id, cap=request.cap, status="running", started_at=now()
        )
    )
    return find_run(db, run_id)


@app.get("/api/runs/latest", operation_id="get_latest_run")
def get_latest_run(db: Database) -> Run | None:
    """Get the newest run with its to-do list, or nothing before the first run."""
    query = select(runs.c.run_id).order_by(runs.c.number.desc()).limit(1)
    run_id = db.execute(query).scalar_one_or_none()
    return None if run_id is None else find_run(db, run_id)


@app.put("/api/runs/{run_id}/todos", operation_id="write_todos")
def write_todos(run_id: str, request: TodosRequest, db: Database) -> Run:
    """Replace the to-do list of a running run."""
    running_run(db, run_id)
    db.execute(delete(todos).where(todos.c.run_id == run_id))
    if request.todos:
        db.execute(
            insert(todos),
            [
                {"run_id": run_id, "position": position, **todo.model_dump()}
                for position, todo in enumerate(request.todos)
            ],
        )
    return find_run(db, run_id)


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: str, request: FinishRequest, db: Database) -> Run:
    """Finish a run with the agent's summary for the handlers."""
    running_run(db, run_id)
    db.execute(
        update(runs)
        .where(runs.c.run_id == run_id)
        .values(status="finished", finished_at=now(), summary=request.summary)
    )
    return find_run(db, run_id)


@app.post("/api/claims/{claim_id}/model-calls", operation_id="record_model_call")
def record_model_call(
    claim_id: str, request: ModelCallRequest, db: Database
) -> dict[str, str]:
    """Record the provider's usage and cost for one received model reply."""
    claim = find_claim(db, claim_id)
    if claim["thread_id"] is None:
        raise HTTPException(409, "Claim has not been started")
    run_records.record_model_call(db, claim_id, claim["thread_id"], request)
    return {"status": "recorded"}


@app.put(
    "/api/claims/{claim_id}/work-slices/{slice_id}", operation_id="record_work_slice"
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
