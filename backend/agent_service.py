"""Forward authenticated backend requests to the agent service."""

import logging
import os
from http import HTTPStatus
from typing import Any

import httpx
from fastapi import HTTPException

from backend import agent_key
from backend.improvement_models import ProposalDecision
from backend.models import Handler

AGENT_URL = os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002")

agent = httpx.Client(base_url=f"{AGENT_URL}/api", timeout=10)
log = logging.getLogger(__name__)


def _response_body(response: httpx.Response) -> dict[str, Any]:
    """Read an agent reply without treating unrelated JSON as a valid reply."""
    try:
        body = response.json()
    except ValueError as error:
        raise HTTPException(
            503, "The agent's service could not start the run"
        ) from error
    if not isinstance(body, dict):
        raise HTTPException(503, "The agent's service could not start the run")
    return body


def _start(path: str, body: dict[str, Any]) -> dict[str, str]:
    """Forward a start without creating backend state before service acceptance."""
    try:
        response = agent.post(path, json=body, headers=agent_key.request_headers())
        response.raise_for_status()
    except httpx.RequestError as error:
        raise HTTPException(503, "The agent's service is unavailable") from error
    except httpx.HTTPStatusError as error:
        if error.response.status_code == HTTPStatus.UNAUTHORIZED:
            raise HTTPException(
                503, "The agent service rejected the backend credential"
            ) from error
        detail = _response_body(error.response).get("detail")
        if detail is None:
            raise HTTPException(
                503, "The agent's service could not start the run"
            ) from error
        raise HTTPException(error.response.status_code, detail) from error
    return _response_body(response)


def start_dispatch(concurrency: int) -> dict[str, str]:
    """Ask the agent service to start a dispatch at the chosen concurrency."""
    return _start("/dispatches", {"concurrency": concurrency})


def resume_claim(claim_id: str) -> None:
    """Hand off a committed handler review without making it depend on the agent."""
    try:
        reply = _start(f"/claims/{claim_id}/resume", {})
        if reply.get("status") != "accepted":
            raise HTTPException(503, "The agent did not acknowledge the handoff")
    except HTTPException as error:
        log.warning(
            "Could not resume claim %s after review: %s", claim_id, error.detail
        )


def start_evaluation(set_name: str, tiny: bool, concurrency: int) -> dict[str, str]:
    """Ask the agent service to evaluate copies from a selected labelled set."""
    return _start(
        "/evaluations", {"set_name": set_name, "tiny": tiny, "concurrency": concurrency}
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
