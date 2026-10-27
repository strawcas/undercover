"""Lifecycle cleanup for explicitly invoked claim and queue commands."""

from http import HTTPStatus

from agent.execution import AttemptBudget, Execution, Runner
from agent.main import AgentRunError, run
from backend.claims import Claims
from backend.diagnostics import diagnose, preserve_primary
from backend.errors import ClaimError
from backend.harness_models import Run, RunFinishInput
from backend.runs import Runs


def reconcile_execution(claims: Claims, execution: Execution) -> None:
    """Close our registered run even if its HTTP service became unavailable."""
    if execution.run_id is None or execution.result is not None:
        return
    runs = Runs(claims.store)
    try:
        current = runs.get(execution.run_id)
    except ClaimError as error:
        if error.status != HTTPStatus.NOT_FOUND:
            raise
        return
    if current["status"] == "running":
        status = "interrupted" if execution.stopping.is_set() else "failed"
        current = runs.finish(
            execution.run_id,
            RunFinishInput(
                status=status,
                detail="Queue execution ended before HTTP cleanup completed.",
            ),
        )
    execution.result = Run.model_validate(current)


def default_runner(
    claim_id: str, thread_id: str, *, budget: AttemptBudget | None = None
) -> str:
    return run(claim_id, thread_id, budget=budget)


def execute(claims: Claims, claim_id: str, thread_id: str, runner: Runner) -> None:
    """Finalize a directly invoked claim helper without interpreting its prose."""
    try:
        runner(claim_id, thread_id, budget=AttemptBudget())
    except AgentRunError as error:
        finish_failed(claims, claim_id, thread_id, str(error), error)
    except Exception as error:
        reference = diagnose("Assess claim directly", error)
        finish_failed(
            claims,
            claim_id,
            thread_id,
            f"Agent run failed ({type(error).__name__}). {reference}.",
            error,
        )
    except KeyboardInterrupt:
        with preserve_primary("Finalize interrupted direct assessment"):
            claims.finish(
                claim_id,
                thread_id,
                "Assessment interrupted. Retry eligible work with uv run -m agent.",
            )
        raise
    else:
        claims.finish(claim_id, thread_id)


def finish_failed(
    claims: Claims, claim_id: str, thread_id: str, detail: str, primary: Exception
) -> None:
    try:
        claims.finish(claim_id, thread_id, detail)
    except Exception as cleanup_error:
        reference = diagnose("Finalize direct assessment", cleanup_error)
        primary.add_note(f"Assessment cleanup also failed. {reference}.")
        raise AgentRunError(detail) from primary
