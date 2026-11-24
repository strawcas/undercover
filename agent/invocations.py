"""Validate the wire format for AgentCore work requests."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agent import service


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Health(Request):
    operation: Literal["health"]


class Dispatch(Request):
    operation: Literal["start_dispatch"]
    agents: int = Field(ge=1, le=25)


class Evaluation(Request):
    operation: Literal["start_evaluation"]
    agents: int = Field(ge=1, le=25)
    set_name: Literal["evaluation", "held_out"]
    tiny: bool


class StartRound(Request):
    operation: Literal["start_round"]
    handler: service.Handler


class StopRound(Request):
    operation: Literal["stop_round"]
    round_id: str = Field(min_length=1)
    handler: service.Handler


class DecideProposal(Request):
    operation: Literal["decide_proposal"]
    round_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    handler: service.Handler
    decision: Literal["approve", "reject"]
    reason: str | None


class ResumeClaim(Request):
    operation: Literal["resume_claim"]
    claim_id: str = Field(min_length=1)


WorkRequest = (
    Dispatch | Evaluation | StartRound | StopRound | DecideProposal | ResumeClaim
)
Invocation = Health | WorkRequest
invocation = TypeAdapter(Annotated[Invocation, Field(discriminator="operation")])


def work(request: WorkRequest, background: service.Scheduler) -> dict[str, str]:
    """Dispatch a validated request to the appropriate work operation."""
    match request:
        case Dispatch():
            response = service.start_dispatch(
                service.DispatchRequest(concurrency=request.agents), background
            )
        case Evaluation():
            response = service.start_evaluation(
                service.EvaluationRequest(
                    concurrency=request.agents,
                    set_name=request.set_name,
                    tiny=request.tiny,
                ),
                background,
            )
        case StartRound():
            response = service.start_round(request.handler, background)
        case StopRound():
            response = service.stop_round(request.round_id, request.handler)
        case DecideProposal():
            response = service.decide_proposal(
                request.round_id,
                request.proposal_id,
                service.HandlerDecision(
                    decision=request.decision,
                    reason=request.reason,
                    handler=request.handler,
                ),
                background,
            )
        case ResumeClaim():
            response = service.resume_claim(request.claim_id, background)
    return {key: value for key, value in response.items() if key != "status"}
