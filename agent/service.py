"""Admit and execute the agent's work for the AgentCore entrypoint."""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from agent import api, checkpoints, improver, improver_api, memory, run_execution
from agent.backend_client import Refused
from agent.harness import (
    cancel_resume,
    carry_on,
    queue_resume,
    release_claim,
    release_review,
    take_resume,
    work_returned,
)

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


class Handler(BaseModel):
    handler_id: str
    name: str
    role: str
    email: str
    can_approve_over_limit: bool


class HandlerDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None
    handler: Handler


class Scheduler(Protocol):
    def add_task[**P](
        self, func: Callable[P, Any], /, *args: P.args, **kwargs: P.kwargs
    ) -> None: ...


def recover() -> None:
    """Close abandoned local runs before admitting another work stretch."""
    checkpoints.clear_threads(api.stop_runs())
    improver_api.recover()


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
        raise Refused(409, f"Cannot start while {slot.owner} is running")
    slot.owner = owner


def accept_run(selection: dict[str, Any], background: Scheduler) -> dict[str, str]:
    """Reserve the single dispatch/evaluation slot before scheduling any work."""
    reserve(selection["kind"])
    try:
        background.add_task(work_run, selection)
    except RuntimeError:
        run_slot.release()
        raise
    return {"status": "accepted"}


def start_dispatch(request: DispatchRequest, background: Scheduler) -> dict[str, str]:
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


def start_evaluation(
    request: EvaluationRequest, background: Scheduler
) -> dict[str, str]:
    """Accept an evaluation in the same single admission slot as dispatches."""
    return accept_run({"kind": "evaluation", **request.model_dump()}, background)


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


def start_round(handler: Handler, background: Scheduler) -> dict[str, str]:
    """Reserve a stretch before creating and scheduling a senior handler's round."""
    reserve("improvement round")
    try:
        current = improver_api.start_round(handler.model_dump())
        background.add_task(work_round, current["round_id"])
    except Exception:
        run_slot.release()
        raise
    return {"status": "accepted", "round_id": current["round_id"]}


def stop_round(round_id: str, handler: Handler) -> dict[str, str]:
    """Mark a round stopped immediately, leaving in-flight work to drain."""
    if not handler.can_approve_over_limit:
        raise Refused(403, "A senior handler must stop rounds")
    improver_api.finish(round_id, True, f"Stopped by {handler.name}")
    return {"status": "accepted"}


def decide_proposal(
    round_id: str,
    proposal_id: str,
    request: HandlerDecision,
    background: Scheduler,
) -> dict[str, str]:
    """Record a decision only after admission, then resume the saved tool call."""
    reserve(f"improvement round {round_id}")
    decision: dict[str, Any] = {"type": request.decision}
    if request.decision == "reject":
        decision["message"] = request.reason
    try:
        improver_api.decide(round_id, proposal_id, request.model_dump())
        background.add_task(work_round, round_id, decision)
    except Exception:
        run_slot.release()
        raise
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


def resume_claim(claim_id: str, background: Scheduler) -> dict[str, str]:
    """Accept a checkpoint resume after a recorded senior handler review."""
    claim = api.get_claim(claim_id)
    payment = claim["payment"]
    sent_back = claim["status"] == api.Status.SENT_BACK
    if not sent_back and (
        claim["status"] != api.Status.DECIDED
        or not payment
        or not payment["signed_off_by"]
    ):
        raise Refused(409, f"Claim {claim_id} has no paid sign-off to resume")
    if not queue_resume(claim):
        raise Refused(409, f"Claim {claim_id} is already being worked")
    owner = str(uuid.uuid4()) if sent_back else None
    try:
        if owner is not None:
            claim = api.take_review(claim_id, owner)
        background.add_task(resume_owned, claim, owner)
    except Exception:
        cancel_resume(claim_id)
        if owner is not None:
            release_review(claim_id, owner)
        raise
    return {"status": "accepted"}
