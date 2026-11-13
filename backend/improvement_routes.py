"""Handler orchestration and internal round operations, kept on distinct routes."""

from fastapi import APIRouter
from pydantic import BaseModel

from backend import agent_service, configurations, improvement_budget, improvements
from backend.auth import CurrentHandler, SeniorHandler
from backend.configuration_models import RecipeHistory
from backend.dependencies import Database
from backend.improvement_models import (
    EvaluationQuote,
    HandlerDecision,
    ProposalDecision,
    ProposalRequest,
    Quote,
    Round,
    RoundProgress,
    RoundUsage,
)
from backend.models import Handler

router = APIRouter()


class CloseRound(BaseModel):
    stopped: bool
    reason: str


@router.get("/api/rounds", operation_id="list_rounds")
def list_rounds(db: Database, handler: CurrentHandler) -> list[Round]:
    """Show signed-in handlers round progress and history."""
    return improvements.list_rounds(db)


@router.get("/api/recipes", operation_id="list_recipes")
def list_recipes(db: Database, handler: CurrentHandler) -> list[RecipeHistory]:
    """Show versioned recipes and their backend-generated diffs."""
    return configurations.recipe_history(db)


@router.post("/api/rounds", operation_id="start_round", status_code=202)
def start_round(handler: SeniorHandler) -> dict[str, str]:
    """Ask the agent service to reserve a stretch for a new round."""
    return agent_service.start_round(handler)


@router.post("/api/rounds/{round_id}/stop", operation_id="stop_round", status_code=202)
def stop_round(round_id: str, handler: SeniorHandler) -> dict[str, str]:
    """Ask the service to stop a round before any further work starts."""
    return agent_service.stop_round(round_id, handler)


@router.post(
    "/api/rounds/{round_id}/proposals/{proposal_id}/decision",
    operation_id="decide_proposal",
    status_code=202,
)
def decide_proposal(
    round_id: str, proposal_id: str, request: ProposalDecision, handler: SeniorHandler
) -> dict[str, str]:
    """Pass a verified handler's approval or rejection to the agent service."""
    return agent_service.decide_proposal(round_id, proposal_id, request, handler)


@router.post("/api/improver/rounds", operation_id="create_round")
def create_round(handler: Handler, db: Database) -> Round:
    """Create a round only after the service has reserved its work slot."""
    return improvements.start(db, handler)


@router.post("/api/improver/recover", operation_id="recover_rounds")
def recover_rounds(db: Database) -> list[str]:
    """Close abandoned stretches while retaining pending approval checkpoints."""
    return improvements.recover(db)


@router.get("/api/improver/rounds/{round_id}", operation_id="get_round")
def get_round(round_id: str, db: Database) -> Round:
    """Read one round for service orchestration without held-out examples."""
    return improvements.get_round(db, round_id)


@router.get("/api/improver/rounds/{round_id}/report", operation_id="read_round_results")
def read_round_results(round_id: str, db: Database) -> dict:
    """Supply the improver's restricted development evidence and aggregates."""
    return improvements.report(db, round_id)


@router.post(
    "/api/improver/rounds/{round_id}/baseline", operation_id="prepare_baseline"
)
def prepare_baseline(round_id: str, db: Database) -> dict:
    """Identify missing baseline runs and freeze a completed baseline."""
    return improvements.baseline(db, round_id)


@router.put(
    "/api/improver/rounds/{round_id}/progress", operation_id="record_round_progress"
)
def record_round_progress(round_id: str, request: RoundProgress, db: Database) -> Round:
    """Persist observable progress from the service's current stretch."""
    return improvements.progress(db, round_id, request)


@router.post(
    "/api/improver/rounds/{round_id}/validate", operation_id="validate_proposal"
)
def validate_proposal(round_id: str, request: ProposalRequest, db: Database) -> dict:
    """Validate a proposal before the human-in-the-loop pause."""
    return {"refusal": improvements.validate_proposal(db, round_id, request)}


@router.post(
    "/api/improver/rounds/{round_id}/proposals", operation_id="record_pending_proposal"
)
def record_pending_proposal(
    round_id: str, request: ProposalRequest, db: Database
) -> Round:
    """Persist the proposal saved in the improver's approval checkpoint."""
    return improvements.pending(db, round_id, request)


@router.post(
    "/api/improver/rounds/{round_id}/proposals/{proposal_id}/decision",
    operation_id="record_proposal_decision",
)
def record_proposal_decision(
    round_id: str, proposal_id: str, request: HandlerDecision, db: Database
) -> Round:
    """Save the handler decision before resuming durable execution."""
    return improvements.decide(db, round_id, proposal_id, request)


@router.post("/api/improver/rounds/{round_id}/quote", operation_id="quote_evaluation")
def quote_evaluation(round_id: str, request: EvaluationQuote, db: Database) -> Quote:
    """Quote a prescribed evaluation against the remaining round budget."""
    return improvements.evaluation_quote(db, round_id, request)


@router.post(
    "/api/improver/rounds/{round_id}/admission", operation_id="admit_improver_call"
)
def admit_improver_call(round_id: str, db: Database) -> dict[str, str]:
    """Check stop and budget immediately before another improver model call."""
    improvement_budget.admit(db, round_id)
    return {"status": "accepted"}


@router.post(
    "/api/improver/rounds/{round_id}/sequence-quote",
    operation_id="quote_candidate_sequence",
)
def quote_candidate_sequence(round_id: str, db: Database) -> Quote:
    """Check the full remaining candidate sequence against its captured budget."""
    return improvements.sequence_quote(db, round_id)


@router.post("/api/improver/rounds/{round_id}/usage", operation_id="record_round_usage")
def record_round_usage(
    round_id: str, request: RoundUsage, db: Database
) -> dict[str, str]:
    """Record idempotent provider billing facts separately from round state."""
    return improvements.record_usage(db, round_id, request)


@router.post(
    "/api/improver/rounds/{round_id}/verdict", operation_id="apply_round_verdict"
)
def apply_round_verdict(round_id: str, db: Database) -> Round:
    """Apply the comparison rule without accepting a service-supplied verdict."""
    return improvements.apply_verdict(db, round_id)


@router.post("/api/improver/rounds/{round_id}/finish", operation_id="finish_round")
def finish_round(round_id: str, request: CloseRound, db: Database) -> Round:
    """Close or stop a round while preserving an existing verdict."""
    return improvements.finish(db, round_id, request.stopped, request.reason)
