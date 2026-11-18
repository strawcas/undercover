"""The shapes of the backend's requests and responses."""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

Status = Literal[
    "waiting", "in_progress", "waiting_for_sign_off", "decided", "left_for_handler"
]
DecisionKind = Literal["approve", "decline", "refer"]
OrdinaryStepKind = Literal["tool_call", "tool_result", "message", "reading", "error"]
StepKind = Literal[OrdinaryStepKind, "security"]
DocumentName = Literal["policy_wording", "claims_manual"]
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


class Decision(BaseModel):
    decision: DecisionKind
    payout: float
    section: str
    reason: str
    decided_at: str
    signed_off_by: str | None
    signed_off_at: str | None


class Payment(BaseModel):
    amount: float
    paid_at: str
    signed_off_by: str | None
    signed_off_at: str | None


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


class Step(BaseModel):
    step_id: int
    thread_id: str
    kind: StepKind
    content: str
    created_at: str


class Section(BaseModel):
    document: DocumentName
    document_title: str
    number: int
    title: str
    text: str


class SectionMatch(BaseModel):
    document: DocumentName
    number: int
    title: str
    excerpts: list[str]


class StartRequest(BaseModel):
    thread_id: str = Field(min_length=1)


class StepRequest(BaseModel):
    kind: OrdinaryStepKind
    content: str = Field(min_length=1)


class SecurityStepRequest(BaseModel):
    kind: Literal["security"]
    thread_id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class DecisionRequest(BaseModel):
    decision: DecisionKind
    payout: float = Field(
        ge=0, description="In US dollars, 0 for a decline or referral"
    )
    section: str = Field(
        min_length=1, description="The sections of the wording and the manual relied on"
    )
    reason: str = Field(
        min_length=1, description="Two or three sentences explaining the decision"
    )

    @model_validator(mode="after")
    def check_payout(self) -> Self:
        """An approval pays something, and a decline or referral pays nothing."""
        if self.decision == "approve" and self.payout == 0:
            raise ValueError("An approval needs a payout above zero")
        if self.decision != "approve" and self.payout != 0:
            raise ValueError("A decline or a referral has a payout of zero")
        return self


class PaymentRequest(BaseModel):
    amount: float = Field(gt=0, description="The approved payout in US dollars")


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


class CalculationStep(BaseModel):
    step: str
    amount: float


class Payout(BaseModel):
    calculation: list[CalculationStep]
    payout: float
    needs_sign_off: bool


class FailureRequest(BaseModel):
    reason: str = Field(min_length=1, description="What went wrong")


RunKind = Literal["dispatch", "evaluation"]
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


class RunAdmission(BaseModel):
    run_id: str | None


class ModelCallRequest(BaseModel):
    reply_id: str = Field(min_length=1)
    run_id: str | None
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    cost: float | None = Field(ge=0)


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
