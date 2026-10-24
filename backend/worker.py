"""Sequential local queue processing shared by the API and terminal modes."""

from collections.abc import Callable
from threading import Event, Thread

import httpx

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
    """Process one queue snapshot, shared by startup and terminal runs."""

    def __init__(
        self,
        claims: Claims,
        runner: Runner,
        ready: Callable[[], bool],
        release: Callable[[], None],
    ):
        self.claims = claims
        self.runner = runner
        self.ready = ready
        self.release = release
        self.attempted: set[str] = set()
        self.stopping = Event()
        self.thread = Thread()

    def step(self, eligible: set[str] | None = None) -> dict | None:
        """Run the next claim and return its saved result, or idle."""
        if self.stopping.is_set():
            return None
        selected = self.claims.start_next(self.attempted, eligible)
        if selected is None:
            return None
        claim_id, thread_id = selected
        self.attempted.add(claim_id)
        execute(self.claims, claim_id, thread_id, self.runner)
        return self.claims.detail(claim_id)

    def run_once(self, eligible: set[str]) -> None:
        try:
            if not eligible:
                return
            while not self.stopping.is_set() and not self.ready():
                self.stopping.wait(0.1)
            while not self.stopping.is_set():
                if self.step(eligible) is None:
                    break
        finally:
            self.release()

    def start(self) -> None:
        eligible = {
            row["claim_id"] for row in self.claims.queue() if row["status"] == "waiting"
        }
        self.thread = Thread(
            target=self.run_once,
            args=(eligible,),
            name="claims-startup",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> bool:
        self.stopping.set()
        self.thread.join(timeout=5)
        return not self.thread.is_alive()
