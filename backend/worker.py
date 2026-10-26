"""Lifecycle cleanup for explicitly invoked claim and queue commands."""

from http import HTTPStatus

from agent.execution import Execution, Runner
from agent.main import AgentRunError, run
from backend.claims import Claims
from backend.errors import ClaimError
from backend.harness_models import RunFinishInput
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
    execution.result = current


def default_runner(claim_id: str, thread_id: str) -> str:
    return run(claim_id, thread_id)


def execute(claims: Claims, claim_id: str, thread_id: str, runner: Runner) -> None:
    """Finalize a directly invoked claim helper without interpreting its prose."""
    try:
        runner(claim_id, thread_id)
    except AgentRunError as error:
        claims.finish(claim_id, thread_id, str(error))
    except Exception as error:
        claims.finish(
            claim_id,
            thread_id,
            f"Agent run failed ({type(error).__name__}). "
            "Check the model configuration and retry.",
        )
    except KeyboardInterrupt:
        claims.finish(
            claim_id, thread_id, "Assessment interrupted. Retry in a new session."
        )
        raise
    else:
        claims.finish(claim_id, thread_id)
