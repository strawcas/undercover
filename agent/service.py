"""Schedule and execute backend-admitted work in this Runtime session."""

import logging
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from functools import partial
from http import HTTPStatus
from json import JSONDecodeError
from typing import Any, Protocol

import httpx

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
from agent.heartbeat import heartbeat

log = logging.getLogger(__name__)
ROUND_FAILURE = "The agent could not finish this round"


class UnconfirmedTake(Exception):
    """The backend may have taken work before its reply was lost."""


class Scheduler(Protocol):
    def add_task[**P](
        self, func: Callable[P, Any], /, *args: P.args, **kwargs: P.kwargs
    ) -> None: ...


def clear_abandoned_evaluations() -> None:
    """Discard only checkpoint threads from evaluations the app marked stopped."""
    try:
        threads = api.stopped_evaluation_threads()
        if threads:
            checkpoints.clear_threads(threads)
    except Exception:
        log.exception(
            "Stopped evaluation checkpoint cleanup will retry with the next work"
        )


def work_run(run: dict) -> None:
    """Work one admitted dispatch or evaluation and report its heartbeats."""
    run_id = run["run_id"]
    try:
        with heartbeat(partial(api.run_heartbeat, run_id)):
            clear_abandoned_evaluations()
            run_execution.work(run)
    finally:
        if run["kind"] == "dispatch":
            memory.recover_pending()


def schedule_run(run_id: str, background: Scheduler) -> None:
    """Take the app's named run before confirming acceptance."""
    schedule(background, partial(api.take_run, run_id), work_run)


def work_round(round_id: str, accepted: dict) -> None:
    """Keep a taken stretch alive through its evaluations."""
    work_id = accepted["work_id"]
    try:
        with heartbeat(partial(improver_api.heartbeat, round_id, work_id)):
            clear_abandoned_evaluations()
            improver.work(round_id, accepted["review"])
    except Exception:
        improver_api.stop_work(round_id, work_id, ROUND_FAILURE)
        raise


def schedule_round(
    round_id: str, proposal_id: str | None, background: Scheduler
) -> None:
    """Take the app's current stretch before confirming acceptance."""
    schedule(
        background,
        partial(improver_api.take_work, round_id, proposal_id),
        partial(work_round, round_id),
    )


def schedule[T](
    background: Scheduler, take: Callable[[], T], work: Callable[[T], None]
) -> None:
    """Confirm ownership only after a tracked worker exists and takes the job."""
    taken: Future[None] = Future()

    def accept_and_work() -> None:
        try:
            job = take()
        except BaseException as error:
            taken.set_exception(error)
            return
        taken.set_result(None)
        work(job)

    background.add_task(accept_and_work)
    try:
        taken.result()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        raise
    except (httpx.RequestError, JSONDecodeError) as error:
        raise UnconfirmedTake() from error
    except Refused as error:
        if error.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise UnconfirmedTake() from error
        raise


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
