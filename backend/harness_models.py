"""Required HTTP contracts for queue runs and claim attempts."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models import State

TodoStatus = Literal["to_do", "in_progress", "done", "left_for_handler"]
Handoff = Literal["referral", "sign_off", "technical_failure", "unpaid_approval"]
RunStatus = Literal["running", "completed", "failed", "interrupted"]
Outcome = Literal["approve", "decline", "refer"]
PaymentStatus = Literal["paid", "eligible", "blocked", "unavailable"]


class RequiredInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApiError(BaseModel):
    detail: str


class AllScope(RequiredInput):
    mode: Literal["all"]


class LimitedScope(RequiredInput):
    mode: Literal["limited"]
    max_claims: Annotated[int, Field(strict=True, gt=0)]


class ClaimScope(RequiredInput):
    mode: Literal["claim"]
    claim_id: str = Field(min_length=1)


Scope = Annotated[AllScope | LimitedScope | ClaimScope, Field(discriminator="mode")]


class RunInput(RequiredInput):
    run_id: UUID
    scope: Scope


class TodoInput(RequiredInput):
    id: str = Field(min_length=1, max_length=100)
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=300)
    status: TodoStatus


class TodosInput(RequiredInput):
    items: list[TodoInput]

    @model_validator(mode="after")
    def unique_items(self) -> Self:
        for field in ("id", "claim_id"):
            values = [getattr(item, field) for item in self.items]
            if len(set(values)) != len(values):
                raise ValueError("To-do IDs and claim IDs must be unique.")
        return self


class Todo(TodoInput):
    handoff_reason: Handoff | None


class TodosResponse(BaseModel):
    run_id: str
    items: list[Todo]
    updated_at: str


class AttemptInput(RequiredInput):
    claim_id: str = Field(min_length=1)
    thread_id: UUID


class AttemptFinishInput(RequiredInput):
    error: str = Field(max_length=2000)


class RunFinishInput(RequiredInput):
    status: Literal["completed", "failed", "interrupted"]
    detail: str = Field(max_length=2000)

    @model_validator(mode="after")
    def valid_detail(self) -> Self:
        if (self.status == "completed") != (self.detail == ""):
            raise ValueError("Only completed runs have an empty finish detail.")
        return self


class Counts(BaseModel):
    worked: int = Field(default=0, ge=0)
    assessment_attempts: int = Field(default=0, ge=0)
    failed_assessments: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    declined: int = Field(default=0, ge=0)
    referred: int = Field(default=0, ge=0)
    simulated_payments: int = Field(default=0, ge=0)
    awaiting_sign_off: int = Field(default=0, ge=0)
    handler_review: int = Field(default=0, ge=0)
    unpaid_approvals: int = Field(default=0, ge=0)
    waiting_remaining: int = Field(default=0, ge=0)


class ClaimResult(BaseModel):
    claim_status: State
    failed_attempts: int
    outcome: Outcome | None
    payout: str | None
    payment_status: PaymentStatus


class Attempt(ClaimResult):
    run_id: str
    claim_id: str
    thread_id: str
    attempt_number: int
    status: Literal["active", "completed", "failed"]
    started_at: str
    deadline_at: str
    finished_at: str | None
    error: str | None


class CompletionCheck(BaseModel):
    run_id: str
    complete: bool
    capacity_remaining: int
    missing_claim_ids: list[str]
    retryable_claim_ids: list[str]
    active_claim_ids: list[str]
    unresolved_todo_ids: list[str]
    counts: Counts


class Summary(BaseModel):
    text: str
    detail: str
    counts: Counts


class Run(BaseModel):
    run_id: str
    scope: Scope
    status: RunStatus
    claim_limit: int
    snapshot_claim_ids: list[str]
    admitted_claim_ids: list[str]
    capacity_remaining: int
    started_at: str
    finished_at: str | None
    todos: list[Todo]
    attempts: list[Attempt]
    counts: Counts
    summary: Summary | None


class CurrentRun(BaseModel):
    run: Run | None


class StoredRun(BaseModel):
    """Persisted coordinator state, excluding the live claim and attempt views."""

    run_id: str
    scope: Scope
    status: RunStatus
    claim_limit: int
    snapshot_claim_ids: list[str]
    admitted_claim_ids: list[str]
    started_at: str
    finished_at: str | None
    todos: list[Todo]
    summary: Summary | None
    counts: Counts = Field(default_factory=Counts)
    final_check: CompletionCheck | None = None
    finish_input: RunFinishInput | None = None
