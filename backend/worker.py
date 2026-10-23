"""A local background task; execution does not survive a server restart."""

from collections.abc import Callable

from agent.main import run
from backend.claims import Claims

Runner = Callable[[str, str], str]


def default_runner(claim_id: str, thread_id: str) -> str:
    return run(claim_id, thread_id)


def execute(claims: Claims, claim_id: str, thread_id: str, runner: Runner) -> None:
    try:
        runner(claim_id, thread_id)
    except Exception as error:
        claims.finish(
            claim_id,
            thread_id,
            f"Agent run failed ({type(error).__name__}). "
            "Check the model configuration and retry.",
        )
    else:
        claims.finish(claim_id, thread_id)
