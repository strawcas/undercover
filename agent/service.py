"""The agent's service: the backend calls it after a handler's sign-off, and it picks
the claim back up. npm run dev starts it beside the backend and the dashboard."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent import api, dispatcher, harness
from agent.console import show_reports
from agent.harness import (
    carry_on,
    checkpointer,
    queue_resume,
    release_claim,
    take_resume,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Keep the claim graph's checkpointer open while the service runs."""
    show_reports()
    with checkpointer() as saver:
        app.state.saver = saver
        await recover()
        yield


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)
run_slot = Lock()
log = logging.getLogger(__name__)


class DispatchRequest(BaseModel):
    concurrency: int = Field(ge=1, le=25)


class EvaluationRequest(DispatchRequest):
    set_name: Literal["evaluation", "held_out"]
    tiny: bool


async def recover() -> None:
    """Wait for the concurrently started backend, then close abandoned runs."""
    while True:
        try:
            await run_in_threadpool(api.health)
            break
        except httpx.RequestError:
            await asyncio.sleep(0.25)
    threads = await run_in_threadpool(api.stop_runs)
    await run_in_threadpool(harness.clear_threads, threads)


def work_run(selection: dict[str, Any]) -> None:
    """Create the accepted run in background and always release its admission slot."""
    run = None
    try:
        configuration = api.get_current_configuration()
        run = api.start_run(
            selection
            | {
                "configuration_version": configuration["version"],
            }
        )
        dispatcher.dispatch(run["run_id"], run["concurrency"])
        api.finish_run(run["run_id"])
    except Exception:
        log.exception("The run stopped before it finished")
        try:
            harness.clear_threads(api.stop_runs())
        except Exception:
            log.exception("Run recovery will be retried at service startup")
    finally:
        if run is not None and run["kind"] == "evaluation":
            try:
                harness.clear_threads(api.get_run_threads(run["run_id"]))
            except Exception:
                log.exception("Evaluation checkpoints will be cleared at startup")
        run_slot.release()


def accept_run(
    selection: dict[str, Any], background: BackgroundTasks
) -> dict[str, str]:
    """Reserve the single dispatch/evaluation slot before scheduling any work."""
    if not run_slot.acquire(blocking=False):
        raise HTTPException(409, "A dispatch or evaluation is already running")
    background.add_task(work_run, selection)
    return {"status": "accepted"}


@app.post("/api/dispatches", operation_id="start_dispatch", status_code=202)
def start_dispatch(
    request: DispatchRequest, background: BackgroundTasks
) -> dict[str, str]:
    """Accept a dispatch immediately or refuse while another run owns the slot."""
    return accept_run(
        {
            "kind": "dispatch",
            "concurrency": request.concurrency,
            "set_name": None,
            "tiny": None,
        },
        background,
    )


@app.post("/api/evaluations", operation_id="start_evaluation", status_code=202)
def start_evaluation(
    request: EvaluationRequest, background: BackgroundTasks
) -> dict[str, str]:
    """Accept an evaluation in the same single admission slot as dispatches."""
    return accept_run({"kind": "evaluation", **request.model_dump()}, background)


@app.exception_handler(api.Refused)
def refused(_: Request, error: api.Refused) -> JSONResponse:
    """Pass on the backend's refusal, such as a claim it doesn't know."""
    return JSONResponse({"detail": error.detail}, status_code=error.status_code)


def resume_owned(claim: dict[str, Any]) -> None:
    """Resume an already reserved claim and always release ownership."""
    claim_id = claim["claim_id"]
    try:
        take_resume(claim_id)
        carry_on(claim, None)
    finally:
        release_claim(claim_id)


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the agent's service is running."""
    return {"status": "ok"}


@app.post("/api/claims/{claim_id}/resume", operation_id="resume_claim", status_code=202)
def resume_claim(claim_id: str, background: BackgroundTasks) -> dict[str, str]:
    """Pick a claim back up after a handler's sign-off, without waiting for it."""
    claim = api.get_claim(claim_id)
    signed_off = claim["decision"] and claim["decision"]["signed_off_by"]
    if claim["status"] != api.Status.IN_PROGRESS or not signed_off:
        raise HTTPException(409, f"Claim {claim_id} has no sign-off waiting to be paid")
    if not queue_resume(claim_id):
        raise HTTPException(409, f"Claim {claim_id} is already being worked")
    background.add_task(resume_owned, claim)
    return {"status": "accepted"}
