"""Invoke the agent locally or on Bedrock AgentCore Runtime."""

import json
import logging
import os
import uuid
from contextlib import closing, suppress
from functools import cache
from http import HTTPStatus
from typing import Any

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionError,
    NoCredentialsError,
    ParamValidationError,
)
from fastapi import HTTPException

from backend import agent_key
from backend.improvement_models import ProposalDecision
from backend.models import Handler

DEFAULT_AGENT = "http://127.0.0.1:8080"
INVOCATIONS = "/invocations"
UNAVAILABLE = "The agent's service is unavailable"
UNCERTAIN = "The agent may have started this work. Reload to see what happened."
RUNTIME_LIMITS = Config(
    retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=20
)
agent = httpx.Client(base_url=os.getenv("UNDERCOVER_AGENT", DEFAULT_AGENT), timeout=10)
log = logging.getLogger(__name__)


class InvocationError(HTTPException):
    """Tell the caller whether the agent may already have accepted the work."""

    def __init__(self, detail: str, status: int = 503, *, uncertain: bool = False):
        super().__init__(status, detail)
        self.uncertain = uncertain


def local_reply(payload: dict[str, Any]) -> bytes:
    """Send one keyed HTTP request to the laptop agent."""
    try:
        response = agent.post(
            INVOCATIONS,
            json=payload,
            headers={"Authorization": f"Bearer {agent_key.configured_key()}"},
        )
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
        httpx.ProxyError,
        httpx.UnsupportedProtocol,
    ) as error:
        raise InvocationError(UNAVAILABLE) from error
    except httpx.RequestError as error:
        raise InvocationError(UNCERTAIN, uncertain=True) from error
    if response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        raise InvocationError("The agent service rejected the backend credential")
    if not response.is_success:
        raise InvocationError("The agent's service could not start the work")
    return response.content


@cache
def runtime_client() -> Any:
    """Use the app's region and allow one attempt per new Runtime session."""
    return boto3.client("bedrock-agentcore", config=RUNTIME_LIMITS)


def runtime_reply(arn: str, payload: dict[str, Any]) -> bytes:
    """Invoke the configured agent in a fresh Runtime session and close the stream."""
    try:
        client = runtime_client()
    except (BotoCoreError, ClientError) as error:
        raise InvocationError(UNAVAILABLE) from error
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            contentType="application/json",
            accept="application/json",
            runtimeSessionId=str(uuid.uuid4()),
            payload=json.dumps(payload).encode(),
        )
    except (ConnectionError, NoCredentialsError, ParamValidationError) as error:
        raise InvocationError(UNAVAILABLE) from error
    except ClientError as error:
        log.warning(
            "Runtime rejected the agent invocation: %s", error.response["Error"]["Code"]
        )
        raise InvocationError("Runtime rejected the agent invocation") from error
    except BotoCoreError as error:
        raise InvocationError(UNCERTAIN, uncertain=True) from error
    return read_runtime_reply(response)


def read_runtime_reply(response: dict[str, Any]) -> bytes:
    """Honor an explicit Runtime error and close any returned body."""
    if response.get("statusCode", HTTPStatus.OK) >= HTTPStatus.BAD_REQUEST:
        if (stream := response.get("response")) is not None:
            with suppress(BotoCoreError, OSError):
                stream.close()
        raise InvocationError("Runtime returned an error from the agent")
    try:
        with closing(response["response"]) as stream:
            return stream.read()
    except (BotoCoreError, OSError, KeyError) as error:
        raise InvocationError(UNCERTAIN, uncertain=True) from error


def invoke(operation: str, **inputs: Any) -> dict[str, Any]:
    """Send one work request and validate the agent's acknowledgement."""
    payload = {"operation": operation, **inputs}
    arn = os.getenv("UNDERCOVER_AGENT_RUNTIME")
    raw = runtime_reply(arn, payload) if arn else local_reply(payload)
    try:
        reply = json.loads(raw)
    except ValueError as error:
        raise InvocationError(UNCERTAIN, uncertain=True) from error
    if isinstance(reply, dict):
        if isinstance(reason := reply.get("refused"), str):
            raise InvocationError(reason, status=409)
        if reply.get("accepted") is True:
            return reply
    raise InvocationError(UNCERTAIN, uncertain=True)


def start_dispatch(concurrency: int) -> dict[str, str]:
    """Ask the agent service to start a dispatch at the chosen concurrency."""
    invoke("start_dispatch", agents=concurrency)
    return {"status": "accepted"}


def resume_claim(claim_id: str) -> None:
    """Hand off a committed handler review without making it depend on the agent."""
    try:
        invoke("resume_claim", claim_id=claim_id)
    except InvocationError as error:
        log.warning(
            "Could not resume claim %s after review: %s", claim_id, error.detail
        )


def start_evaluation(set_name: str, tiny: bool, concurrency: int) -> dict[str, str]:
    """Ask the agent service to evaluate copies from a selected labelled set."""
    invoke("start_evaluation", set_name=set_name, tiny=tiny, agents=concurrency)
    return {"status": "accepted"}


def start_round(handler: Handler) -> dict[str, str]:
    """Ask the service to start a round with the verified senior handler."""
    reply = invoke("start_round", handler=handler.model_dump())
    if not isinstance(round_id := reply.get("round_id"), str):
        raise InvocationError(UNCERTAIN, uncertain=True)
    return {"status": "accepted", "round_id": round_id}


def stop_round(round_id: str, handler: Handler) -> dict[str, str]:
    """Ask the service to mark a round stopped before draining its work."""
    invoke("stop_round", round_id=round_id, handler=handler.model_dump())
    return {"status": "accepted"}


def decide_proposal(
    round_id: str, proposal_id: str, decision: ProposalDecision, handler: Handler
) -> dict[str, str]:
    """Forward a proposal decision without writing an approval prematurely."""
    invoke(
        "decide_proposal",
        round_id=round_id,
        proposal_id=proposal_id,
        handler=handler.model_dump(),
        **decision.model_dump(),
    )
    return {"status": "accepted"}
