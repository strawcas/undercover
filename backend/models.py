"""The shapes of the backend's requests and responses."""

from datetime import date
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Status = Literal[
    "waiting",
    "in_progress",
    "waiting_for_sign_off",
    "sent_back",
    "decided",
    "left_for_handler",
]
DecisionKind = Literal["approve", "decline", "refer"]
RunKind = Literal["dispatch", "evaluation"]
SECTION_14 = "Claims manual section 14"
OrdinaryStepKind = Literal["tool_call", "tool_result", "message", "reading", "error"]
StepKind = Literal[
    OrdinaryStepKind,
    "security",
    "lookup",
    "memory_search",
    "memory_save",
    "refusal",
    "claim_review",
]
Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]
LineKind = Literal[
    "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
]


class Item(BaseModel):
    description: str
    category: str
    amount: float


class Document(BaseModel):
    name: str
    type: str
    content: str


class CalculationStep(BaseModel):
    step: str
    amount: float


class PayoutCalculation(BaseModel):
    calculation: list[CalculationStep]
    payout: float


class Decision(BaseModel):
    decision: DecisionKind
    payout: float
    section: str
    reason: str
    calculation: PayoutCalculation | None
    decided_by: str
    decided_at: str
    manual_version: str
    signed_off_by: str | None
    signed_off_at: str | None
    gateway_hold: str | None


class Payment(BaseModel):
    amount: float
    paid_at: str
    signed_off_by: str | None
    signed_off_at: str | None


class SendBackRequest(BaseModel):
    note: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


class ClaimReview(SendBackRequest):
    handler: str
    reviewed_at: str


class ReturnedDecision(ClaimReview):
    decision: Decision
    approval_limit: float


class ClaimWorkOwner(BaseModel):
    owner: str = Field(min_length=1)


class TakeClaimRequest(ClaimWorkOwner):
    run_id: str | None


class ClaimHeartbeat(ClaimWorkOwner):
    working: bool


class ClaimSummary(BaseModel):
    claim_id: str
    customer_name: str
    claim_type: str
    summary: str
    amount_claimed: float
    submitted_on: str
    status: Status
    decision: DecisionKind | None
    payout: float | None
    review_active: bool


class Claim(BaseModel):
    claim_id: str
    policy_id: str
    customer_id: str
    customer_name: str
    claim_type: str
    submitted_on: str
    incident_date: str
    incident_location: str
    summary: str
    story: str
    items: list[Item]
    amount_claimed: float
    documents: list[Document]
    status: Status
    thread_id: str | None
    attempts: int
    decision: Decision | None
    payment: Payment | None
    sign_off_available: bool
    evaluation_id: str | None
    source_id: str | None
    returned_decisions: list[ReturnedDecision]
    revision: int
    review_active: bool


class Trip(BaseModel):
    destination: str
    departs_on: str
    returns_on: str
    trip_cost: float


class Policy(BaseModel):
    policy_id: str
    customer_id: str
    plan: str
    add_ons: list[str]
    purchased_on: str
    sold_through: str
    trip: Trip
    travelers: list[str]
    status: str


class PreviousClaim(BaseModel):
    claim_id: str
    filed_on: str
    incident_date: str
    claim_type: str
    summary: str
    outcome: str
    amount_paid: float


class Handler(BaseModel):
    handler_id: str
    name: str
    role: str
    email: str
    can_approve_over_limit: bool


class Source(BaseModel):
    file: str
    system: str
    owner: str
    reviewed: date


class Lookup(BaseModel):
    subject: str
    sources: list[Source] = Field(min_length=1)


class MemorySearch(BaseModel):
    query: str
    results: list[str]


class MemoryMessage(BaseModel):
    role: Literal["customer", "agent"]
    text: str = Field(min_length=1)


class MemorySave(BaseModel):
    messages: list[MemoryMessage]


class Refusal(BaseModel):
    source: Literal["Gateway", "App", "Context layer", "Customer memory", "Tool"]
    reason: str


StepContent = str | Lookup | MemorySearch | MemorySave | Refusal | ClaimReview


class Step(BaseModel):
    step_id: int
    thread_id: str
    kind: StepKind
    content: StepContent
    created_at: str


class RunOwnership(BaseModel):
    run_id: str = Field(description="The owning run, or an empty string")

    @model_validator(mode="before")
    @classmethod
    def legacy_run(cls, data: Any) -> Any:
        """Allow previously issued requests without an owning run."""
        return {"run_id": "", **data} if isinstance(data, dict) else data


class StartRequest(RunOwnership):
    thread_id: str = Field(min_length=1)


class DecisionRevision(BaseModel):
    revision: int = Field(ge=0, description="The current decision revision")

    @model_validator(mode="before")
    @classmethod
    def legacy_revision(cls, data: Any) -> Any:
        """Allow older callers to write only before the first review."""
        return {"revision": 0, **data} if isinstance(data, dict) else data


class DecisionAttemptRequest(StartRequest, DecisionRevision):
    pass


class LimitHoldRequest(DecisionRevision, RunOwnership):
    pass


class FinishMemoryRequest(DecisionAttemptRequest):
    messages: list[MemoryMessage] = Field(min_length=1, max_length=2)

    @field_validator("messages")
    @classmethod
    def unique_roles(cls, messages: list[MemoryMessage]) -> list[MemoryMessage]:
        """Accept each acknowledged event role at most once per request."""
        if len({message.role for message in messages}) != len(messages):
            raise ValueError("Provide one saved event per role")
        return messages


class StepRequest(StartRequest):
    kind: OrdinaryStepKind
    content: str = Field(min_length=1)


class SecurityStepRequest(StartRequest):
    kind: Literal["security"]
    content: str = Field(min_length=1)


class LookupStepRequest(StartRequest):
    kind: Literal["lookup"]
    content: Lookup


class MemorySearchStepRequest(StartRequest):
    kind: Literal["memory_search"]
    content: MemorySearch


class RefusalStepRequest(StartRequest):
    kind: Literal["refusal"]
    content: Refusal


NewStepRequest = (
    StepRequest
    | SecurityStepRequest
    | LookupStepRequest
    | MemorySearchStepRequest
    | RefusalStepRequest
)


class DecisionRequest(DecisionRevision, RunOwnership):
    decision: DecisionKind
    thread_id: str = Field(
        min_length=1, description="The attempt recording the decision"
    )
    payout: float = Field(
        allow_inf_nan=False, description="In US dollars, 0 for a decline or referral"
    )
    section: str = Field(
        min_length=1, description="The sections of the wording and the manual relied on"
    )
    reason: str = Field(
        min_length=1, description="Two or three sentences explaining the decision"
    )
    decided_by: str = Field(min_length=1, description="Who or what made the decision")

    @model_validator(mode="before")
    @classmethod
    def require_record_fields(cls, data: Any) -> Any:
        """Check the required text before looking up the claim."""
        if isinstance(data, dict):
            for field in ("decision", "payout"):
                if data.get(field) is None:
                    raise ValueError(f"{SECTION_14}: {field} is required")
            for field in ("section", "reason", "decided_by", "thread_id"):
                value = data.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"{SECTION_14}: {field} is required and cannot be blank"
                    )
        return data

    @model_validator(mode="after")
    def check_payout(self) -> Self:
        """An approval pays something, and a decline or referral pays nothing."""
        if self.decision == "approve" and round(self.payout, 2) <= 0:
            raise ValueError(
                f"{SECTION_14}: An approval needs a payout of at least one cent"
            )
        if self.decision != "approve" and self.payout != 0:
            raise ValueError(
                f"{SECTION_14}: A decline or a referral has a payout of zero"
            )
        return self


class PaymentRequest(DecisionRevision, RunOwnership):
    amount: float = Field(gt=0, description="The approved payout in US dollars")


class GatewayHoldRequest(DecisionRevision, RunOwnership):
    reason: str = Field(min_length=1, description="The Gateway's payment refusal")


class Plan(BaseModel):
    plan: str
    name: str
    trip_cancellation_max: float
    trip_delay_waiting_hours: int
    trip_delay_max: float
    baggage_delay_waiting_hours: int
    baggage_delay_max: float
    baggage_max: float
    per_item_limit: float
    passport_max: float
    emergency_medical_max: float
    deductible: float


class AddOn(BaseModel):
    add_on: str
    name: str
    effect: str
    electronics_per_item_limit: float | None = None


class Plans(BaseModel):
    plans: list[Plan]
    deductible_applies_to: list[Benefit]
    add_ons: list[AddOn]


class Line(BaseModel):
    description: str = Field(min_length=1)
    amount: float = Field(ge=0, description="The total claimed, before limits")
    quantity: int = Field(ge=1, description="How many separate items")
    kind: LineKind
    nights: int = Field(ge=0, description="For a hotel, and 0 otherwise")
    hours: float = Field(
        ge=0, description="For meals without receipts, and 0 otherwise"
    )


class PayoutRequest(BaseModel):
    benefit: Benefit
    lines: list[Line] = Field(
        description="The covered lines, with any excluded left out"
    )
    paid_by_others: float = Field(ge=0, description="Paid or refunded by others")


class ClaimPayoutRequest(PayoutRequest, DecisionRevision, RunOwnership):
    thread_id: str | None = Field(description="The attempt, or null for a preview")


class Payout(PayoutCalculation):
    needs_sign_off: bool


class StoppedWork(BaseModel):
    reason: str = Field(min_length=1)


class FailureRequest(DecisionRevision, RunOwnership):
    reason: str = Field(min_length=1, description="What went wrong")


SetName = Literal["evaluation", "held_out"]
MemberStatus = Literal["selected", "finished", "skipped", "in_progress"]


class RunMember(BaseModel):
    claim_id: str
    customer_name: str
    status: MemberStatus
    error: str | None
    claim_status: Status
    decision: DecisionKind | None
    payout: float | None
    reason: str | None
    llm_cost: float | None
    active_seconds: float
    started_at: str | None
    finished_at: str | None
    elapsed_seconds: float | None


class Run(BaseModel):
    run_id: str
    kind: RunKind
    concurrency: int
    model: str
    configuration_version: int
    round_id: str | None
    reasoning_effort: str | None
    set_name: SetName | None
    tiny: bool | None
    status: Literal["running", "finished", "stopped"]
    stop_reason: str | None = None
    started_at: str
    finished_at: str | None
    summary: str | None
    members: list[RunMember]
    llm_cost: float | None
    active_seconds: float
    seconds: float


class DispatchRequest(BaseModel):
    concurrency: int = Field(ge=1, le=25)


class EvaluationRequest(DispatchRequest):
    set_name: SetName
    tiny: bool


class RunRequest(BaseModel):
    kind: RunKind
    concurrency: int = Field(ge=1, le=25)
    configuration_version: int = Field(ge=1)
    round_id: str | None
    set_name: SetName | None
    tiny: bool | None

    @model_validator(mode="after")
    def check_selection(self) -> Self:
        if self.kind == "evaluation" and (self.set_name is None or self.tiny is None):
            raise ValueError("An evaluation needs a set and tiny/full selection")
        if self.kind == "dispatch" and (
            self.set_name is not None
            or self.tiny is not None
            or self.round_id is not None
        ):
            raise ValueError("A dispatch has no evaluation selection")
        return self


class RunClaimRequest(BaseModel):
    status: MemberStatus
    error: str | None


class RunSelectionRequest(BaseModel):
    claim_ids: list[str]


class RunAdmission(DecisionRevision):
    run_id: str | None


class ModelCallRequest(BaseModel):
    reply_id: str = Field(min_length=1)
    run_id: str | None
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    cost: float | None = Field(ge=0)


class ClaimModelCall(ModelCallRequest):
    thread_id: str

    @model_validator(mode="before")
    @classmethod
    def legacy_thread(cls, data: Any) -> Any:
        """Use the current thread for previously issued receipts."""
        return {"thread_id": "", **data} if isinstance(data, dict) else data


class WorkSliceRequest(BaseModel):
    run_id: str | None
    seconds: float = Field(ge=0, allow_inf_nan=False)


class Scores(BaseModel):
    grade: float | None
    decision_accuracy: float
    accuracy: float
    cost_of_mistakes: float
    straight_through: float
    model_cost_per_claim: float | None
    seconds_per_claim: float


class Comparison(RunMember):
    source_id: str
    handler_decision: DecisionKind
    handler_payout: float
    handler_reason: str
    handler_reference: str
    agent_section: str | None
    grade: float | None
    letter: Literal["F", "C", "B", "A"] | None
    same_grounds: float | None
    same_section: float | None
    right_decision: bool
    right: bool
    mistake_cost: float


class Evaluation(Run):
    scores: Scores
    judge_cost: float | None


class EvaluationDetail(Evaluation):
    comparisons: list[Comparison]
