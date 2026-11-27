"""Expose handler-scoped Copilot conversations and worker progress."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from backend import copilot_records as records
from backend import copilot_service
from backend.auth import Credentials, CurrentHandler, VerifiedEmail, handler_for_email
from backend.copilot_models import (
    ConfirmationAnswer,
    Conversation,
    ConversationDetail,
    Message,
    Outcome,
    StepUpdate,
    Stretch,
    TurnRef,
    Work,
)
from backend.database import engine
from backend.dependencies import Database

router = APIRouter(prefix="/api/copilot/conversations")


@router.get("", operation_id="list_copilot_conversations")
def conversations(handler: CurrentHandler, db: Database) -> list[Conversation]:
    """List this handler's earlier Copilot conversations."""
    return records.list_for(db, handler.handler_id)


@router.post("", operation_id="create_copilot_conversation", status_code=201)
def create(handler: CurrentHandler, db: Database) -> Conversation:
    """Start an empty Copilot conversation for the signed-in handler."""
    return records.create(db, handler.handler_id)


@router.get("/{conversation_id}", operation_id="get_copilot_conversation")
def conversation(
    conversation_id: UUID, handler: CurrentHandler, db: Database
) -> ConversationDetail:
    """Read the messages, progress and confirmation for an owned conversation."""
    return records.detail(db, handler.handler_id, str(conversation_id))


@router.post(
    "/{conversation_id}/messages", operation_id="send_copilot_message", status_code=202
)
def message(
    conversation_id: UUID,
    request: Message,
    email: VerifiedEmail,
    credentials: Credentials,
) -> dict[str, str]:
    """Admit a message and start its turn after committing the conversation."""
    if credentials is None:
        raise HTTPException(401, "Sign in to continue")
    identity = str(conversation_id)
    with engine().begin() as db:
        owner = handler_for_email(email, db).handler_id
        message = request.message.replace(credentials.credentials, "[sign-in removed]")
        turn_id, stretch_id = records.admit(db, owner, identity, message.strip())
    copilot_service.launch(
        "start", owner, TurnRef(identity, turn_id), stretch_id, credentials
    )
    return {"turn_id": turn_id}


@router.post(
    "/{conversation_id}/confirmation",
    operation_id="answer_copilot_confirmation",
    status_code=202,
)
def confirmation(
    conversation_id: UUID,
    request: ConfirmationAnswer,
    email: VerifiedEmail,
    credentials: Credentials,
) -> dict[str, str]:
    """Accept one response to the pending confirmation and resume its turn."""
    if credentials is None:
        raise HTTPException(401, "Sign in to continue")
    identity, turn_id = str(conversation_id), str(request.turn_id)
    with engine().begin() as db:
        owner = handler_for_email(email, db).handler_id
        stretch_id = records.answer_confirmation(
            db, owner, identity, turn_id, request.confirmed
        )
    copilot_service.launch(
        "resume", owner, TurnRef(identity, turn_id), stretch_id, credentials
    )
    return {"turn_id": turn_id}


@router.post(
    "/{conversation_id}/turns/{turn_id}/take", operation_id="take_copilot_turn"
)
def take(
    conversation_id: UUID,
    turn_id: UUID,
    request: Stretch,
    handler: CurrentHandler,
    db: Database,
) -> Work:
    """Let one worker take the active stretch as its verified owner."""
    return records.claim(
        db,
        handler.handler_id,
        str(conversation_id),
        str(turn_id),
        str(request.stretch_id),
    )


def step_path(
    conversation_id: UUID, turn_id: UUID, step_id: UUID
) -> tuple[TurnRef, str]:
    """Bind a step's routing IDs from the path."""
    return TurnRef(str(conversation_id), str(turn_id)), str(step_id)


@router.put(
    "/{conversation_id}/turns/{turn_id}/steps/{step_id}",
    operation_id="record_copilot_step",
    status_code=204,
)
def step(
    path: Annotated[tuple[TurnRef, str], Depends(step_path)],
    request: StepUpdate,
    handler: CurrentHandler,
    db: Database,
) -> None:
    """Record or complete a progress step for the current worker."""
    ref, step_id = path
    records.record_step(db, handler.handler_id, ref, step_id, request)


@router.put(
    "/{conversation_id}/turns/{turn_id}/outcome",
    operation_id="finish_copilot_stretch",
    status_code=204,
)
def finish(
    conversation_id: UUID,
    turn_id: UUID,
    request: Outcome,
    handler: CurrentHandler,
    db: Database,
) -> None:
    """Record the outcome of the current working stretch."""
    records.finish(db, handler.handler_id, str(conversation_id), str(turn_id), request)
