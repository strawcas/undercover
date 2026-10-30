"""HTTP entry point for explicit queues and saved handler sign-offs."""

from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from time import monotonic, sleep

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import TypeAdapter

from agent.checkpoints import checkpointer
from agent.execution import Runner
from agent.http import RequestOptions, request
from agent.main import backend
from agent.service_worker import ModelFactory, Worker
from backend.claim_models import Health, QueueRow, Recorded
from backend.config import (
    agent_owner_path,
    agent_url,
    database_identity,
    database_target,
    load_environment,
)
from backend.diagnostics import preserve_primary
from backend.harness_models import ApiError, Attempt, CurrentRun, Run, RunInput
from backend.local import local_address
from backend.ownership import DatabaseOwner


def verify_backend(identity: str) -> None:
    """Wait for concurrent development startup and reject another database."""
    deadline = monotonic() + 20
    while True:
        try:
            with backend() as client:
                response = client.get("/api/health", timeout=1)
            break
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if monotonic() >= deadline:
                raise RuntimeError(
                    "The matching UnderCover backend is unavailable."
                ) from None
            sleep(0.1)
    if (
        not response.is_success
        or response.headers.get("X-UnderCover-Database") != identity
    ):
        raise RuntimeError("The backend is serving another service or database.")


@contextmanager
def resources(runner: Runner | None) -> Iterator[BaseCheckpointSaver | None]:
    """Share one production pool; injected SQLite runners own their saver."""
    if runner is None:
        with checkpointer(service_owner=True) as saver:
            yield saver
    else:
        target = database_target()
        if not isinstance(target, Path):
            raise ValueError(
                "Injected service runners require an isolated SQLite database."
            )
        with DatabaseOwner(agent_owner_path(target)):
            yield None


def recover_service_work() -> None:
    """Suspend the previous owner's work before starting the exclusive worker."""
    detail = "The agent service restarted; an explicit command continues saved work."
    with backend() as client:
        response = request(client, "GET", "/api/agent-runs/current")
        response.raise_for_status()
        parent = CurrentRun.model_validate(response.json()).run
        if parent and parent.status == "running":
            request(
                client,
                "POST",
                f"/api/agent-runs/{parent.run_id}/finish",
                {"status": "interrupted", "detail": detail},
                options=RequestOptions(
                    headers={
                        "X-UnderCover-Run-Generation": str(parent.execution_generation)
                    }
                ),
            ).raise_for_status()
        response = request(client, "GET", "/api/claims")
        response.raise_for_status()
        for claim in TypeAdapter(list[QueueRow]).validate_python(response.json()):
            if claim.run_active:
                response = request(
                    client, "GET", f"/api/claims/{claim.claim_id}/execution"
                )
                response.raise_for_status()
                entry = Attempt.model_validate(response.json())
                request(
                    client,
                    "POST",
                    f"/api/agent-runs/{entry.run_id}/attempts/{entry.thread_id}/suspend",
                    {"detail": detail},
                    options=RequestOptions(
                        headers={"X-UnderCover-Execution": entry.execution_id or ""}
                    ),
                ).raise_for_status()


def create_app(
    runner: Runner | None = None, model_factory: ModelFactory | None = None
) -> FastAPI:
    """Create a single-owner service without admitting claims at startup."""
    identity = database_identity(database_target())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        verify_backend(identity)
        with resources(runner) as saver:
            recover_service_work()
            worker = Worker(runner, model_factory, saver)
            app.state.worker = worker
            worker.thread.start()
            try:
                yield
            finally:
                with preserve_primary("Stop agent service worker"):
                    worker.close()

    app = FastAPI(
        title="UnderCover Agent",
        lifespan=lifespan,
        responses={409: {"model": ApiError}},
    )

    @app.exception_handler(httpx.HTTPStatusError)
    async def backend_error(_request, error: httpx.HTTPStatusError):
        status = error.response.status_code
        detail = error.response.json().get("detail") if status in {404, 409} else None
        return JSONResponse(
            status_code=status if status in {404, 409} else 503,
            content={
                "detail": detail
                if isinstance(detail, str)
                else "The backend is unavailable."
            },
        )

    @app.get("/health", operation_id="agent_health", response_model=Health)
    def health(response: Response):
        """Check the agent service and its configured database identity."""
        response.headers["X-UnderCover-Service"] = "agent"
        response.headers["X-UnderCover-Database"] = identity
        return {"status": "ok"}

    @app.post("/runs", operation_id="start_queue", response_model=Run, status_code=202)
    def start_queue(body: RunInput):
        """Register an explicit queue command and acknowledge its saved scope."""
        return app.state.worker.submit(body)

    @app.post(
        "/claims/{claim_id}/resume",
        operation_id="resume_claim",
        response_model=Recorded,
        status_code=202,
    )
    def resume_claim(claim_id: str):
        """Resume a saved signed-off approval on its original assessment thread."""
        claim = app.state.worker.resume(claim_id)
        if not claim.sign_off:
            raise HTTPException(409, "No handler sign-off is saved for this claim.")
        return {"status": "recorded"}

    @app.post(
        "/runs/{run_id}/stop",
        operation_id="stop_queue",
        response_model=Recorded,
        status_code=202,
    )
    def stop_queue(run_id: str):
        """Suspend a registered queue while retaining its saved scope and claims."""
        if not app.state.worker.stop(run_id):
            raise HTTPException(404, "The agent service is not executing this run.")
        return {"status": "recorded"}

    return app


if __name__ == "__main__":
    load_environment()
    host, port = local_address(agent_url())
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
