"""Sequential local queue processing shared by the API and terminal modes."""

from collections.abc import Callable
from threading import Event, Thread
from uuid import uuid4

import httpx

from agent.main import AgentRunError, run
from backend.claims import Claims
from backend.harness_models import AttemptInput, RunFinishInput, RunInput, TodosInput
from backend.runs import Runs

Runner = Callable[[str, str], str]


def default_runner(claim_id: str, thread_id: str) -> str:
    return run(claim_id, thread_id)


def execute(claims: Claims, claim_id: str, thread_id: str, runner: Runner) -> None:
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
        self.run_id: str | None = None
        self.finished = False

    def step(self, eligible: set[str] | None = None) -> dict | None:
        """Run the next claim and return its saved result, or idle."""
        if self.stopping.is_set() or self.finished:
            return None
        waiting = [
            row["claim_id"]
            for row in self.claims.queue()
            if row["status"] == "waiting"
            and row["claim_id"] not in self.attempted
            and (eligible is None or row["claim_id"] in eligible)
        ]
        if not waiting:
            self._finish_run()
            return None
        runs = Runs(self.claims.store)
        if self.run_id is None:
            scope = (
                {"mode": "claim", "claim_id": waiting[0]}
                if eligible is not None and len(eligible) == 1
                else {"mode": "all"}
            )
            data, _ = runs.create(
                RunInput.model_validate({"run_id": str(uuid4()), "scope": scope})
            )
            self.run_id = str(data["run_id"])
        claim_id = waiting[0]
        data = runs.get(self.run_id)
        items = [
            {key: item[key] for key in ("id", "claim_id", "text", "status")}
            for item in data["todos"]
        ]
        items.append(
            dict(
                id=f"claim:{claim_id}",
                claim_id=claim_id,
                text=f"Assess {claim_id}",
                status="to_do",
            )
        )
        runs.todos(self.run_id, TodosInput.model_validate({"items": items}))
        entry, _ = runs.start(
            self.run_id, AttemptInput(claim_id=claim_id, thread_id=uuid4())
        )
        thread_id = entry["thread_id"]
        self.attempted.add(claim_id)
        execute(self.claims, claim_id, thread_id, self.runner)
        return self.claims.detail(claim_id)

    def _finish_run(self) -> None:
        if self.run_id is not None and not self.finished:
            runs = Runs(self.claims.store)
            complete = runs.check(self.run_id)["complete"]
            status = "completed" if complete else "failed"
            detail = "" if complete else "Queue pass ended with undecided work."
            if self.stopping.is_set() and not complete:
                status, detail = "interrupted", "Queue processing was interrupted."
            runs.finish(
                self.run_id,
                RunFinishInput.model_validate({"status": status, "detail": detail}),
            )
            self.finished = True

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
            self._finish_run()
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
        if self.thread.ident is None:
            return True
        self.thread.join(timeout=5)
        return not self.thread.is_alive()
