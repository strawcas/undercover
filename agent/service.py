"""Run the local agent service alongside the backend and dashboard."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent import api, checkpoints, improver, improver_api, memory, run_execution
from agent.backend_client import Refused
from agent.checkpoints import checkpointer
from agent.console import show_reports
from agent.harness import (
    cancel_resume,
    carry_on,
    queue_resume,
    release_claim,
    take_resume,
    work_returned,
)
from backend import agent_key
from backend.auth import require_agent
from backend.improvement_models import HandlerDecision
from backend.models import Handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Keep the claim graph's checkpointer open while the service runs."""
    if not agent_key.configured_key():
        raise RuntimeError(f"{agent_key.ENV_NAME} is missing")
    show_reports()
    with checkpointer() as saver:
        app.state.saver = saver
        await recover()
        recovery = asyncio.create_task(run_in_threadpool(memory.recover_pending))
        try:
            yield
        finally:
            recovery.cancel()
            with suppress(asyncio.CancelledError):
                await recovery


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)
internal = APIRouter(dependencies=[Depends(require_agent)])
run_slot = Lock()


@dataclass
class Slot:
    owner: str = "another operation"


slot = Slot()
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
    await run_in_threadpool(checkpoints.clear_threads, threads)
    await run_in_threadpool(improver_api.recover)


def work_run(selection: dict[str, Any]) -> None:
    """Create the accepted run in background and always release its admission slot."""
    try:
        configuration = api.get_current_configuration()
        run_execution.execute(
            selection
            | {
                "configuration_version": configuration["version"],
                "round_id": None,
            }
        )
    except Exception:
        log.exception("The run stopped before it finished")
    finally:
        run_slot.release()
    if selection["kind"] == "dispatch":
        memory.recover_pending()


def reserve(owner: str) -> None:
    """Name the current work while holding the service's single admission lock."""
    if not run_slot.acquire(blocking=False):
        raise HTTPException(409, f"Cannot start while {slot.owner} is running")
    slot.owner = owner


def accept_run(
    selection: dict[str, Any], background: BackgroundTasks
) -> dict[str, str]:
    """Reserve the single dispatch/evaluation slot before scheduling any work."""
    reserve(selection["kind"])
    background.add_task(work_run, selection)
    return {"status": "accepted"}


@internal.post("/api/dispatches", operation_id="start_dispatch", status_code=202)
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


@internal.post("/api/evaluations", operation_id="start_evaluation", status_code=202)
def start_evaluation(
    request: EvaluationRequest, background: BackgroundTasks
) -> dict[str, str]:
    """Accept an evaluation in the same single admission slot as dispatches."""
    return accept_run({"kind": "evaluation", **request.model_dump()}, background)


@app.exception_handler(Refused)
def refused(_: Request, error: Refused) -> JSONResponse:
    """Pass on the backend's refusal, such as a claim it doesn't know."""
    return JSONResponse({"detail": error.detail}, status_code=error.status_code)


def work_round(round_id: str, decision: dict | None = None) -> None:
    """Hold the work slot until all nested work drains or an approval pauses it."""
    try:
        improver.work(round_id, decision)
    except Exception:
        log.exception("The improvement stretch stopped")
        try:
            current = improver_api.get_round(round_id)
            if current["status"] not in ("finished", "stopped"):
                improver_api.finish(
                    round_id, True, "The improvement stretch failed; see service logs"
                )
        except Exception:
            log.exception("Round recovery will be retried at startup")
    finally:
        run_slot.release()


@internal.post("/api/rounds", operation_id="start_round", status_code=202)
def start_round(handler: Handler, background: BackgroundTasks) -> dict[str, str]:
    """Reserve a stretch before creating and scheduling a senior handler's round."""
    reserve("improvement round")
    try:
        current = improver_api.start_round(handler.model_dump())
    except Exception:
        run_slot.release()
        raise
    background.add_task(work_round, current["round_id"])
    return {"status": "accepted", "round_id": current["round_id"]}


@internal.post(
    "/api/rounds/{round_id}/stop", operation_id="stop_round", status_code=202
)
def stop_round(round_id: str, handler: Handler) -> dict[str, str]:
    """Mark a round stopped immediately, leaving in-flight work to drain."""
    if not handler.can_approve_over_limit:
        raise HTTPException(403, "A senior handler must stop rounds")
    improver_api.finish(round_id, True, f"Stopped by {handler.name}")
    return {"status": "accepted"}


@internal.post(
    "/api/rounds/{round_id}/proposals/{proposal_id}/decision",
    operation_id="decide_proposal",
    status_code=202,
)
def decide_proposal(
    round_id: str,
    proposal_id: str,
    request: HandlerDecision,
    background: BackgroundTasks,
) -> dict[str, str]:
    """Record a decision only after admission, then resume the saved tool call."""
    reserve(f"improvement round {round_id}")
    try:
        improver_api.decide(round_id, proposal_id, request.model_dump())
    except Exception:
        run_slot.release()
        raise
    decision: dict[str, Any] = {"type": request.decision}
    if request.decision == "reject":
        decision["message"] = request.reason
    background.add_task(work_round, round_id, decision)
    return {"status": "accepted"}


def resume_owned(claim: dict[str, Any], owner: str | None = None) -> None:
    """Resume an already reserved claim and always release ownership."""
    claim_id = claim["claim_id"]
    try:
        take_resume(claim_id)
        if owner is None:
            carry_on(claim, None)
        else:
            work_returned(claim, owner, None, None)
    finally:
        release_claim(claim_id)


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the agent's service is running."""
    return {"status": "ok"}


@internal.post(
    "/api/claims/{claim_id}/resume", operation_id="resume_claim", status_code=202
)
def resume_claim(claim_id: str, background: BackgroundTasks) -> dict[str, str]:
    """Accept a checkpoint resume after a recorded senior handler review."""
    claim = api.get_claim(claim_id)
    payment = claim["payment"]
    sent_back = claim["status"] == api.Status.SENT_BACK
    if not sent_back and (
        claim["status"] != api.Status.DECIDED
        or not payment
        or not payment["signed_off_by"]
    ):
        raise HTTPException(409, f"Claim {claim_id} has no paid sign-off to resume")
    if not queue_resume(claim):
        raise HTTPException(409, f"Claim {claim_id} is already being worked")
    owner = str(uuid.uuid4()) if sent_back else None
    try:
        if owner is not None:
            claim = api.take_review(claim_id, owner)
    except Exception:
        cancel_resume(claim_id)
        raise
    background.add_task(resume_owned, claim, owner)
    return {"status": "accepted"}


app.include_router(internal)
