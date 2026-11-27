"""Launch committed Copilot work through the AgentCore invocation protocol."""

import os
from time import sleep

import httpx
from fastapi.security import HTTPAuthorizationCredentials

from backend import agent_service, copilot_records
from backend.copilot_models import Operation, TurnRef
from backend.database import engine

DEFAULT_COPILOT = "http://127.0.0.1:8081"
copilot = httpx.Client(
    base_url=os.getenv("UNDERCOVER_COPILOT", DEFAULT_COPILOT), timeout=8
)
LAUNCH_FAILED = "The Copilot could not start. Try again."


def runtime_reply(arn: str, conversation_id: str, payload: dict[str, str]) -> bytes:
    """Retry only a Runtime session conflict that happened before delivery."""
    for delay in (0.5, 1, 2):
        try:
            return agent_service.runtime_reply(arn, payload, conversation_id)
        except agent_service.RuntimeConflict:
            sleep(delay)
    return agent_service.runtime_reply(arn, payload, conversation_id)


def launch(
    operation: Operation,
    owner: str,
    ref: TurnRef,
    stretch_id: str,
    credential: HTTPAuthorizationCredentials,
) -> None:
    """Forward the live bearer after commit and compensate definite rejections."""
    payload = {
        "operation": operation,
        "conversation_id": ref.conversation_id,
        "turn_id": ref.turn_id,
        "stretch_id": stretch_id,
        "bearer": credential.credentials,
    }
    try:
        arn = os.getenv("UNDERCOVER_COPILOT_RUNTIME")
        raw = (
            runtime_reply(arn, ref.conversation_id, payload)
            if arn
            else agent_service.http_reply(copilot, payload, {})
        )
        agent_service.accepted(raw)
    except agent_service.InvocationError as error:
        if not error.uncertain:
            with engine().begin() as db:
                copilot_records.launch_failed(db, owner, ref, stretch_id, LAUNCH_FAILED)
            raise agent_service.InvocationError(LAUNCH_FAILED) from error
