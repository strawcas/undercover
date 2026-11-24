"""Handler orchestration and internal round operations, kept on distinct routes."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend import configurations, improvement_budget, improvements, work_admission
from backend.auth import CurrentHandler, ReviewHandler, require_agent
from backend.configuration_models import RecipeHistory
from backend.database import engine
from backend.dependencies import Database
from backend.improvement_models import (
    EvaluationQuote,
    HandlerDecision,
    ProposalDecision,
    ProposalRequest,
    Quote,
    Round,
    RoundHeartbeat,
    RoundProgress,
    RoundTake,
    RoundUsage,
    StopRoundWork,
)

router = APIRouter()
service_router = APIRouter(dependencies=[Depends(require_agent)])


class CloseRound(BaseModel):
    stopped: bool
    reason: str


@router.get("/api/rounds", operation_id="list_rounds")
def list_rounds(handler: CurrentHandler, db: Database) -> list[Round]:
    """Show signed-in handlers round progress and history."""
    return improvements.list_rounds(db)


@router.get("/api/recipes", operation_id="list_recipes")
def list_recipes(handler: CurrentHandler, db: Database) -> list[RecipeHistory]:
    """Show versioned recipes and their backend-generated diffs."""
    return configurations.recipe_history(db)


@router.post("/api/rounds", operation_id="start_round", status_code=202)
def start_round(handler: ReviewHandler) -> dict[str, str]:
    """Commit a new round with pinned settings before invoking the agent."""
    with engine().begin() as db:
        work_admission.admit(db)
        current = improvements.start(db, handler)
    work_admission.launch(
        "work_round",
        {"round_id": current.round_id},
        lambda db, reason: work_admission.cancel_round_start(
            db, current.round_id, reason
        ),
    )
    return {"status": "accepted", "round_id": current.round_id}


@router.post("/api/rounds/{round_id}/stop", operation_id="stop_round", status_code=202)
def stop_round(round_id: str, handler: ReviewHandler, db: Database) -> dict[str, str]:
    """Stop the named round and its running evaluation."""
    work_admission.lock(db)
    work_admission.stop_round(db, round_id, f"Stopped by {handler.name}")
    return {"status": "accepted"}


@router.post(
    "/api/rounds/{round_id}/proposals/{proposal_id}/decision",
    operation_id="decide_proposal",
    status_code=202,
)
def decide_proposal(
    round_id: str, proposal_id: str, request: ProposalDecision, handler: ReviewHandler
) -> dict[str, str]:
    """Commit the verified review before asking the agent to carry the round on."""
    with engine().begin() as db:
        work_admission.admit(db)
        work_id = improvements.queue_review(
            db,
            round_id,
            proposal_id,
            HandlerDecision(**request.model_dump(), handler=handler),
        )
    work_admission.launch(
        "carry_round_on",
        {"round_id": round_id, "proposal_id": proposal_id},
        lambda db, _: improvements.cancel_review(db, round_id, work_id),
    )
    return {"status": "accepted"}


@service_router.post("/api/improver/rounds/{round_id}/take", operation_id="take_round")
def take_round(round_id: str, request: RoundTake, db: Database) -> dict:
    """Let a worker take the round's committed stretch and any recorded review."""
    work_admission.lock(db)
    return improvements.take_work(db, round_id, request.proposal_id)


@service_router.put(
    "/api/improver/rounds/{round_id}/heartbeat", operation_id="round_heartbeat"
)
def round_heartbeat(
    round_id: str, request: RoundHeartbeat, db: Database
) -> dict[str, str]:
    """Renew only the current working stretch of the named round."""
    work_admission.lock(db)
    improvements.heartbeat(db, round_id, request.work_id)
    return {"status": "recorded"}


@service_router.post(
    "/api/improver/rounds/{round_id}/stop-work", operation_id="stop_round_work"
)
def stop_round_work(round_id: str, request: StopRoundWork, db: Database) -> Round:
    """Stop only the named worker's stretch and its evaluation."""
    work_admission.lock(db)
    work_admission.stop_round(db, round_id, request.reason, work_id=request.work_id)
    return improvements.get_round(db, round_id)


@service_router.get("/api/improver/rounds/{round_id}", operation_id="get_round")
def get_round(round_id: str, db: Database) -> Round:
    """Read one round for service orchestration without held-out examples."""
    return improvements.get_round(db, round_id)


@service_router.get(
    "/api/improver/rounds/{round_id}/report", operation_id="read_round_results"
)
def read_round_results(round_id: str, db: Database) -> dict:
    """Supply the improver's restricted development evidence and aggregates."""
    return improvements.report(db, round_id)


@service_router.post(
    "/api/improver/rounds/{round_id}/baseline", operation_id="prepare_baseline"
)
def prepare_baseline(round_id: str, db: Database) -> dict:
    """Identify missing baseline runs and freeze a completed baseline."""
    return improvements.baseline(db, round_id)


@service_router.put(
    "/api/improver/rounds/{round_id}/progress", operation_id="record_round_progress"
)
def record_round_progress(round_id: str, request: RoundProgress, db: Database) -> Round:
    """Persist observable progress from the service's current stretch."""
    return improvements.progress(db, round_id, request)


@service_router.post(
    "/api/improver/rounds/{round_id}/validate", operation_id="validate_proposal"
)
def validate_proposal(round_id: str, request: ProposalRequest, db: Database) -> dict:
    """Validate a proposal before the human-in-the-loop pause."""
    return {"refusal": improvements.validate_proposal(db, round_id, request)}


@service_router.post(
    "/api/improver/rounds/{round_id}/proposals", operation_id="record_pending_proposal"
)
def record_pending_proposal(
    round_id: str, request: ProposalRequest, db: Database
) -> Round:
    """Persist the proposal saved in the improver's approval checkpoint."""
    return improvements.pending(db, round_id, request)


@service_router.post(
    "/api/improver/rounds/{round_id}/quote", operation_id="quote_evaluation"
)
def quote_evaluation(round_id: str, request: EvaluationQuote, db: Database) -> Quote:
    """Quote a prescribed evaluation against the remaining round budget."""
    return improvements.evaluation_quote(db, round_id, request)


@service_router.post(
    "/api/improver/rounds/{round_id}/admission", operation_id="admit_improver_call"
)
def admit_improver_call(round_id: str, db: Database) -> dict[str, str]:
    """Check stop and budget immediately before another improver model call."""
    improvement_budget.admit(db, round_id)
    return {"status": "accepted"}


@service_router.post(
    "/api/improver/rounds/{round_id}/sequence-quote",
    operation_id="quote_candidate_sequence",
)
def quote_candidate_sequence(round_id: str, db: Database) -> Quote:
    """Check the full remaining candidate sequence against its captured budget."""
    return improvements.sequence_quote(db, round_id)


@service_router.post(
    "/api/improver/rounds/{round_id}/usage", operation_id="record_round_usage"
)
def record_round_usage(
    round_id: str, request: RoundUsage, db: Database
) -> dict[str, str]:
    """Record idempotent provider billing facts separately from round state."""
    return improvements.record_usage(db, round_id, request)


@service_router.post(
    "/api/improver/rounds/{round_id}/verdict", operation_id="apply_round_verdict"
)
def apply_round_verdict(round_id: str, db: Database) -> Round:
    """Apply the comparison rule without accepting a service-supplied verdict."""
    return improvements.apply_verdict(db, round_id)


@service_router.post(
    "/api/improver/rounds/{round_id}/finish", operation_id="finish_round"
)
def finish_round(round_id: str, request: CloseRound, db: Database) -> Round:
    """Close or stop a round while preserving an existing verdict."""
    if request.stopped:
        work_admission.lock(db)
        work_admission.stop_round(db, round_id, request.reason)
        return improvements.get_round(db, round_id)
    return improvements.finish(db, round_id, False, request.reason)


router.include_router(service_router)
