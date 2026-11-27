"""Contracts for the Copilot's conversations, progress and handler reads."""

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator

from backend.models import Run, Status

TurnStatus = Literal["working", "waiting", "done", "failed", "stopped"]
StepStatus = Literal["working", "done", "refused", "failed"]
StepKind = Literal["claim", "customer", "context", "work", "memory", "action"]
Operation = Literal["start", "resume"]
STEP_RESULT_LIMIT = 6000


@dataclass(frozen=True)
class TurnRef:
    conversation_id: str
    turn_id: str


@dataclass(frozen=True)
class ActionReceipt:
    ref: TurnRef
    stretch_id: str
    action_id: str

    @classmethod
    def from_header(cls, header: str) -> "ActionReceipt":
        """Parse the conversation, turn, stretch and action receipt."""
        conversation_id, turn_id, stretch_id, action_id = header.split("/")
        return cls(
            TurnRef(str(UUID(conversation_id)), str(UUID(turn_id))),
            str(UUID(stretch_id)),
            str(UUID(action_id)),
        )


class Conversation(BaseModel):
    conversation_id: str
    handler_id: str
    title: str | None
    updated_at: str


class Message(BaseModel):
    message: str = Field(min_length=1, max_length=4000, pattern=r"\S")


class Confirmation(BaseModel):
    claim_id: str
    customer_name: str
    amount: float
    reason: str
    revision: int


class ConfirmationAnswer(BaseModel):
    turn_id: UUID
    confirmed: bool


class Usage(BaseModel):
    model: str
    calls: int = Field(ge=0, le=25)
    cost: float | None
    seconds: float = Field(ge=0)


class StepContent(BaseModel):
    tool: str = Field(max_length=100)
    kind: StepKind
    input: str = Field(max_length=600)
    status: StepStatus
    result: JsonValue
    reason: str | None = Field(max_length=600)
    seconds: float | None

    @field_validator("result")
    @classmethod
    def bounded_result(cls, value: JsonValue) -> JsonValue:
        """Keep persisted cards small enough to return with the conversation."""
        if len(json.dumps(value, ensure_ascii=False)) > STEP_RESULT_LIMIT:
            raise ValueError("The Copilot step result is too large")
        return value


class StepUpdate(StepContent):
    stretch_id: UUID


class Step(StepContent):
    step_id: str
    started_at: str


class Turn(BaseModel):
    turn_id: str
    message: str
    status: TurnStatus
    started_at: str
    answer: str | None
    failure: str | None
    confirmation: Confirmation | None
    usage: Usage | None
    steps: list[Step]


class ConversationDetail(Conversation):
    turns: list[Turn]


class Stretch(BaseModel):
    stretch_id: UUID


class Work(BaseModel):
    message: str
    prior_turn_id: str | None
    confirmation: Confirmation | None
    confirmed: bool | None
    usage: Usage | None


class Outcome(Stretch):
    status: Literal["waiting", "done", "failed"]
    answer: str | None = Field(max_length=30000)
    failure: str | None = Field(max_length=600)
    confirmation: Confirmation | None
    usage: Usage | None


class CustomerMatch(BaseModel):
    customer_id: str
    name: str


class Customer(CustomerMatch):
    customer_since: str | None
    policy_ids: list[str]
    claim_ids: list[str]


class HandoffClaim(BaseModel):
    claim_id: str
    customer_name: str
    status: Status
    reason: str | None


class Dispatch(Run):
    held: list[HandoffClaim]
    referred: list[HandoffClaim]
    left_for_handler: list[HandoffClaim]
    returned: list[HandoffClaim]


class PayoutCoverage(BaseModel):
    limit: float
    paid: int
    signed_off: int
