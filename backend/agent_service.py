"""The agent's service, as the backend calls it: once after each sign-off."""

import logging
import os
from typing import Any

import httpx
from fastapi import HTTPException

from backend.improvement_models import ProposalDecision
from backend.models import Handler

AGENT_URL = os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002")

agent = httpx.Client(base_url=f"{AGENT_URL}/api", timeout=10)

log = logging.getLogger(__name__)


def _start(path: str, body: dict[str, Any]) -> dict[str, str]:
    """Forward a start without creating backend state before service acceptance."""
    try:
        response = agent.post(path, json=body)
        response.raise_for_status()
    except httpx.RequestError as error:
        raise HTTPException(503, "The agent's service is unavailable") from error
    except httpx.HTTPStatusError as error:
        if "application/json" not in error.response.headers.get("content-type", ""):
            raise HTTPException(
                503, "The agent's service could not start the run"
            ) from error
        raise HTTPException(
            error.response.status_code, error.response.json()["detail"]
        ) from error
    return response.json()


def start_dispatch(concurrency: int) -> dict[str, str]:
    """Ask the agent service to start a dispatch at the chosen concurrency."""
    return _start("/dispatches", {"concurrency": concurrency})


def start_evaluation(set_name: str, tiny: bool, concurrency: int) -> dict[str, str]:
    """Ask the agent service to evaluate copies from a selected labelled set."""
    return _start(
        "/evaluations", {"set_name": set_name, "tiny": tiny, "concurrency": concurrency}
    )


def resume_claim(claim_id: str) -> None:
    """Ask the agent to pick a claim back up after its sign-off."""
    try:
        agent.post(f"/claims/{claim_id}/resume").raise_for_status()
    except httpx.HTTPError as error:  # the sign-off stands without the agent
        log.warning(
            "The agent didn't pick up %s after its sign-off: %s", claim_id, error
        )


def start_round(handler: Handler) -> dict[str, str]:
    """Ask the service to start a round with the verified senior handler."""
    return _start("/rounds", handler.model_dump())


def stop_round(round_id: str, handler: Handler) -> dict[str, str]:
    """Ask the service to mark a round stopped before draining its work."""
    return _start(f"/rounds/{round_id}/stop", handler.model_dump())


def decide_proposal(
    round_id: str, proposal_id: str, decision: ProposalDecision, handler: Handler
) -> dict[str, str]:
    """Forward a proposal decision without writing an approval prematurely."""
    return _start(
        f"/rounds/{round_id}/proposals/{proposal_id}/decision",
        decision.model_dump() | {"handler": handler.model_dump()},
    )
