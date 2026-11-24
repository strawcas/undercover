"""Durable rounds, approval requests and their restricted service inputs."""

from typing import Literal

from pydantic import BaseModel, Field

from backend.models import Handler, ModelCallRequest, Scores
from backend.settings_models import AllowedModel, PlatformSettings

RoundStatus = Literal[
    "baseline",
    "working",
    "awaiting_configuration",
    "evaluating",
    "awaiting_recipe",
    "finished",
    "stopped",
]


class ProposalRequest(BaseModel):
    proposal_id: str = Field(min_length=1)
    kind: Literal["configuration", "recipe"]
    arguments: dict


class Proposal(ProposalRequest):
    diff: str
    created_at: str
    decision: Literal["approve", "reject"] | None
    feedback: str | None
    handler: str | None
    decided_at: str | None
    version: int | None


class ProposalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None


class HandlerDecision(ProposalDecision):
    handler: Handler


class Spending(BaseModel):
    improver: float
    claims: float
    judge: float
    total: float
    remaining: float
    unknown: bool


class Round(BaseModel):
    round_id: str
    thread_id: str
    status: RoundStatus
    started_at: str
    started_by: str
    finished_at: str | None
    base_version: int
    candidate_version: int | None
    recipe_version: int
    settings: PlatformSettings
    models_allowed: dict[str, AllowedModel]
    improver_model: str
    improver_effort: str
    progress: int
    before: Scores | None
    after: Scores | None
    verdict: Literal["kept", "rolled_back"] | None
    explanation: str | None
    stop_reason: str | None = None
    work_expired: bool = False
    pending_proposal_id: str | None = None
    proposals: list[Proposal]
    spending: Spending


class RoundProgress(BaseModel):
    status: Literal["baseline", "working", "evaluating"]
    progress: int = Field(ge=0, le=3)


class RoundTake(BaseModel):
    proposal_id: str | None


class RoundHeartbeat(BaseModel):
    work_id: str = Field(min_length=1)


class StopRoundWork(RoundHeartbeat):
    reason: str = Field(min_length=1)


class RoundUsage(ModelCallRequest):
    pass


class EvaluationQuote(BaseModel):
    set_name: Literal["evaluation", "held_out"]
    configuration_version: int


class Quote(BaseModel):
    estimate: float | None
    remaining: float
    allowed: bool
    reason: str
