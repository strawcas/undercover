"""Explicit, renewable execution ownership for a synchronous claim graph."""

from http import HTTPStatus
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

import httpx

from agent.execution import AttemptBudget, QueueStopped
from agent.http import request
from backend.diagnostics import diagnose, preserve_primary
from backend.harness_models import Attempt


class ClaimLease:
    def __init__(
        self, client: httpx.Client, claim_id: str, thread_id: str, budget: AttemptBudget
    ):
        self.client = client
        self.path = f"/api/claims/{claim_id}/execution"
        self.body = {"thread_id": thread_id, "owner_id": str(uuid4())}
        self.budget = budget
        self.stopped = Event()
        self.thread: Thread | None = None
        self.entry: Attempt | None = None
        self.mode = "release"

    def __enter__(self):
        deadline = monotonic() + 7
        while True:
            response = request(self.client, "POST", self.path, self.body)
            if response.status_code != HTTPStatus.CONFLICT or monotonic() >= deadline:
                break
            self.budget.check()
            sleep(0.1)
        if response.status_code == HTTPStatus.CONFLICT:
            raise QueueStopped("Saved work is still owned; it remains resumable.")
        response.raise_for_status()
        self.entry = Attempt.model_validate(response.json())
        if self.entry.owner_id != self.body["owner_id"]:
            return self
        self.budget.execution_id = self.entry.execution_id or ""
        self.budget.deadline = monotonic() + self.entry.remaining_seconds
        self.client.headers["X-UnderCover-Execution"] = self.budget.execution_id
        self.thread = Thread(target=self.heartbeat, name="claim-lease", daemon=True)
        self.thread.start()
        return self

    def heartbeat(self) -> None:
        while not self.stopped.wait(1):
            try:
                response = self.client.put(
                    f"{self.path}/{self.budget.execution_id}", json=self.body, timeout=3
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                diagnose("Renew claim execution", error)
                self.budget.cancelled.set()
                return

    def __exit__(self, error_type, _error, _traceback) -> None:
        self.stopped.set()
        if self.thread:
            with preserve_primary("Stop claim execution heartbeat"):
                self.thread.join(timeout=4)
        if self.entry is None or self.entry.owner_id != self.body["owner_id"]:
            return
        if error_type and issubclass(error_type, (KeyboardInterrupt, QueueStopped)):
            self.mode = "suspend"
        with preserve_primary("Release claim execution"):
            response = request(
                self.client,
                "POST",
                f"{self.path}/{self.budget.execution_id}/release",
                self.body | {"mode": self.mode},
            )
            response.raise_for_status()
