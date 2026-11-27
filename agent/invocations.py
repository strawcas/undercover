"""Validate and route the AgentCore work protocol."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agent import service


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Health(Request):
    operation: Literal["health"]


class WorkRun(Request):
    operation: Literal["work_run"]
    run_id: str = Field(min_length=1)


class WorkRound(Request):
    operation: Literal["work_round"]
    round_id: str = Field(min_length=1)


class CarryRound(Request):
    operation: Literal["carry_round_on"]
    round_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)


class ResumeClaim(Request):
    operation: Literal["resume_claim"]
    claim_id: str = Field(min_length=1)


WorkRequest = WorkRun | WorkRound | CarryRound | ResumeClaim
Invocation = Health | WorkRequest
invocation = TypeAdapter(Annotated[Invocation, Field(discriminator="operation")])


def work(request: WorkRequest, background: service.Scheduler) -> None:
    """Admit a validated job by the IDs already committed by the app."""
    match request:
        case WorkRun():
            service.schedule_run(request.run_id, background)
        case WorkRound():
            service.schedule_round(request.round_id, None, background)
        case CarryRound():
            service.schedule_round(request.round_id, request.proposal_id, background)
        case ResumeClaim():
            service.resume_claim(request.claim_id, background)
