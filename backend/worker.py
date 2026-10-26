"""Run one queue agent after HTTP readiness, retaining database ownership."""

from collections.abc import Callable
from http import HTTPStatus
from threading import Thread

import httpx
from langchain_core.language_models import BaseChatModel

from agent.execution import Execution, Runner
from agent.main import AgentRunError, run
from agent.queue import run_queue
from backend.claims import Claims
from backend.config import run_scope
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


def api_ready(url: str, instance_id: str) -> bool:
    """Wait for this backend instance, not another process on the same address."""
    try:
        response = httpx.get(f"{url}/api/health", timeout=0.5)
    except httpx.HTTPError:
        return False
    return (
        response.is_success
        and response.headers.get("X-UnderCover-Instance") == instance_id
    )


class QueueWorker:
    """Own one background queue-agent invocation, never a claim selection loop."""

    def __init__(
        self,
        claims: Claims,
        runner: Runner,
        ready: Callable[[], bool],
        release: Callable[[], None],
        queue_model: BaseChatModel | None = None,
    ):
        self.claims = claims
        self.runner = runner
        self.ready = ready
        self.release = release
        self.queue_model = queue_model
        self.execution = Execution()
        self.stopping = self.execution.stopping
        self.thread = Thread()
        self.scope: dict = {}
        self.result: dict | None = None
        self.error = ""

    def run_once(self) -> None:
        try:
            while not self.stopping.is_set() and not self.ready():
                self.stopping.wait(0.1)
            if not self.stopping.is_set():
                self.result = run_queue(
                    self.scope, self.runner, self.queue_model, self.execution
                )
        except (AgentRunError, ValueError, httpx.HTTPError) as error:
            self.error = str(error)
        except KeyboardInterrupt:
            self.error = "Queue processing was interrupted."
        finally:
            reconcile_execution(self.claims, self.execution)
            self.result = self.execution.result
            self.release()

    def start(self) -> None:
        self.scope = run_scope()
        self.thread = Thread(target=self.run_once, name="claims-startup", daemon=True)
        self.thread.start()

    def stop(self) -> bool:
        self.stopping.set()
        if self.thread.ident is None:
            return True
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.execution.cancel_active.set()
        return not self.thread.is_alive()
