"""One sequential worker owns queue commands and saved claim continuations."""

from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, PriorityQueue
from threading import Event, Lock, Thread

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import TypeAdapter

from agent.execution import AttemptBudget, Execution, Runner
from agent.http import request
from agent.main import backend, run
from agent.queue import run_queue
from agent.queue_tools import QueueTools
from backend.claim_models import ClaimDetail
from backend.diagnostics import diagnose, preserve_primary
from backend.harness_models import Attempt, Run, RunInput

ModelFactory = Callable[[], BaseChatModel]


@dataclass(frozen=True)
class QueueJob:
    registered: Run
    execution: Execution


@dataclass(frozen=True)
class ResumeJob:
    claim_id: str


class Worker:
    def __init__(
        self,
        runner: Runner | None,
        model_factory: ModelFactory | None,
        saver: BaseCheckpointSaver | None,
    ):
        self.runner = runner or self.assess
        self.model_factory = model_factory
        self.saver = saver
        self.jobs: PriorityQueue[tuple[int, int, QueueJob | ResumeJob]] = (
            PriorityQueue()
        )
        self.lock = Lock()
        self.keys: set[str] = set()
        self.sequence = 0
        self.executions: dict[str, Execution] = {}
        self.stopped = Event()
        self.active: Execution | None = None
        self.thread = Thread(target=self.work, name="claim-agent", daemon=True)

    def assess(self, claim: str, thread: str, *, budget: AttemptBudget) -> str:
        return run(claim, thread, budget=budget, saver=self.saver)

    def enqueue(self, key: str, job: QueueJob | ResumeJob) -> None:
        with self.lock:
            if key in self.keys:
                return
            self.sequence += 1
            self.keys.add(key)
            self.jobs.put((0 if isinstance(job, ResumeJob) else 1, self.sequence, job))

    def submit(self, body: RunInput) -> Run:
        with backend() as client:
            response = request(
                client, "POST", "/api/agent-runs", body.model_dump(mode="json")
            )
            response.raise_for_status()
            registered = Run.model_validate(response.json())
        if registered.status == "running":
            with self.lock:
                execution = self.executions.setdefault(
                    registered.run_id, Execution(run_id=registered.run_id)
                )
            self.enqueue(registered.run_id, QueueJob(registered, execution))
        return registered

    def resume(self, claim_id: str) -> ClaimDetail:
        with backend() as client:
            response = request(client, "GET", f"/api/claims/{claim_id}")
            response.raise_for_status()
            claim = ClaimDetail.model_validate(response.json())
        if claim.sign_off and not claim.payment:
            self.enqueue(claim_id, ResumeJob(claim_id))
        return claim

    def stop(self, run_id: str) -> bool:
        with self.lock:
            execution = self.executions.get(run_id)
            if execution is None:
                return False
            execution.stopping.set()
            execution.cancel_active.set()
        return True

    def continue_claim(self, claim_id: str, execution: Execution) -> None:
        with backend() as client:
            response = request(client, "GET", f"/api/claims/{claim_id}")
            response.raise_for_status()
            claim = ClaimDetail.model_validate(response.json())
            if not claim.sign_off or claim.payment:
                return
            response = request(client, "GET", "/api/claim-executions/pending")
            response.raise_for_status()
            pending = TypeAdapter(list[Attempt]).validate_python(response.json())
            for entry in pending:
                if entry.claim_id == claim_id:
                    parent = request(client, "GET", f"/api/agent-runs/{entry.run_id}")
                    parent.raise_for_status()
                    registered = Run.model_validate(parent.json())
                    QueueTools(
                        client,
                        entry.run_id,
                        self.runner,
                        execution,
                        registered.execution_generation,
                    ).work_attempt(entry, resume=True)

    def execute(self, job: QueueJob | ResumeJob) -> None:
        execution = job.execution if isinstance(job, QueueJob) else Execution()
        self.active = execution
        if self.stopped.is_set():
            execution.stopping.set()
            execution.cancel_active.set()
        try:
            if isinstance(job, ResumeJob):
                self.continue_claim(job.claim_id, execution)
            elif execution.stopping.is_set():
                with backend() as client:
                    QueueTools(
                        client,
                        job.registered.run_id,
                        self.runner,
                        execution,
                        job.registered.execution_generation,
                    ).finish_run("interrupted", "Queue processing was interrupted.")
            else:
                run_queue(
                    job.registered.scope,
                    self.runner,
                    self.model_factory()
                    if self.model_factory and job.registered.claim_limit
                    else None,
                    execution,
                    job.registered,
                )
        finally:
            try:
                with preserve_primary("Wait for cancelled claim execution"):
                    if execution.active_thread:
                        execution.active_thread.join(timeout=125)
                        if execution.active_thread.is_alive():
                            self.stopped.set()
                            raise RuntimeError(
                                "The previous claim execution is still stopping."
                            )
            finally:
                self.active = None

    def reconcile_failure(self, job: QueueJob | ResumeJob, reference: str) -> None:
        if isinstance(job, QueueJob) and job.execution.result is None:
            with backend() as client:
                tools = QueueTools(
                    client,
                    job.registered.run_id,
                    self.runner,
                    job.execution,
                    job.registered.execution_generation,
                )
                current = tools.current()
                job.execution.result = (
                    tools.finish_run(
                        "interrupted", f"Agent service execution stopped. {reference}."
                    )
                    if current.status == "running"
                    else current
                )

    def work(self) -> None:
        while not self.stopped.is_set() or not self.jobs.empty():
            try:
                _, _, job = self.jobs.get(timeout=0.1)
            except Empty:
                continue
            key = job.claim_id if isinstance(job, ResumeJob) else job.registered.run_id
            try:
                self.execute(job)
            except (Exception, KeyboardInterrupt) as error:
                reference = diagnose("Execute agent service job", error)
                with preserve_primary("Reconcile agent service job"):
                    self.reconcile_failure(job, reference)
            finally:
                with self.lock:
                    self.keys.discard(key)
                    if isinstance(job, QueueJob):
                        self.executions.pop(key, None)
                self.jobs.task_done()

    def close(self) -> None:
        self.stopped.set()
        with self.lock:
            for execution in self.executions.values():
                execution.stopping.set()
                execution.cancel_active.set()
            if self.active:
                self.active.stopping.set()
                self.active.cancel_active.set()
        self.thread.join(timeout=130)
        if self.thread.is_alive():
            raise RuntimeError(
                "The agent worker did not stop before its request timeout."
            )
